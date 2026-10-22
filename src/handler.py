"""Lambda entry point + orchestrator.

Pipeline:
  1. Build Google queries for Reddit subs
  2. SerpApi search + Jina Reader enrich -> list[Post]
  3. Drop URLs already in dedup store
  4. Score each post with Claude (scout pass — score + candidate brief)
  5. For score >= threshold, draft an actual comment with Claude
     (drafter pass — brand-voice-compliant comment text)
  6. Send a Slack card per qualifying lead (brief + drafted comment +
     Replied / Skip buttons, tracked in src/leads.py)
  7. Mark all seen URLs so we don't re-score tomorrow
  8. Post a daily run-summary card to Slack (counts + score buckets)
"""

from __future__ import annotations

import logging

from .config import EXTRA_QUERIES, KEYWORD_CLUSTERS, SUBREDDITS, load_config
from .drafter import draft_all
from .leads import LeadsDB
from .models import Post
from .scorer import Scored, score_all
from .slack import (
    RunSummary,
    bucketize_scores,
    post_analyzed_urls_thread,
    send_summary_to_slack,
    send_lead_via_bot,
    send_summary_via_bot,
    send_to_slack,
)
from .sources.search import build_queries, fetch_all
from .store import build_store, url_key

log = logging.getLogger()
log.setLevel(logging.INFO)


def deliver_lead(cfg, leads: LeadsDB, s: Scored, drafted: str) -> bool:
    """Post a lead card. With a bot token the card gets Replied / Skip buttons
    and is tracked in the leads table; otherwise it falls back to the webhook."""
    if cfg.slack_bot_token and cfg.slack_channel_id:
        key = url_key(s.post.url)
        result = send_lead_via_bot(cfg.slack_bot_token, cfg.slack_channel_id, s, key, drafted)
        if result.get("ok"):
            leads.add_lead(s, result["channel"], result["ts"])
            return True
        log.warning("bot lead post failed (%s); falling back to webhook", result.get("error"))
    return send_to_slack(cfg.slack_webhook_url, s, drafted_comment=drafted)


def run() -> dict:
    cfg = load_config()
    store = build_store(cfg.dedup_backend, cfg.dynamodb_table, cfg.aws_region)
    leads = LeadsDB()

    errors: list[str] = []

    if not cfg.serpapi_key:
        msg = "SERPAPI_KEY missing; cannot fetch"
        log.error(msg)
        errors.append(msg)
        summary = RunSummary(0, 0, 0, bucketize_scores([]), 0, cfg.score_threshold, errors)
        send_summary_to_slack(cfg.slack_webhook_url, summary)
        return {"fetched": 0, "fresh": 0, "scored": 0, "sent": 0}

    queries = build_queries(SUBREDDITS, KEYWORD_CLUSTERS, EXTRA_QUERIES, max_or_terms=cfg.max_or_terms)
    log.info("running %d search queries (max %d OR terms each)", len(queries), cfg.max_or_terms)

    raw: list[Post] = fetch_all(
        serpapi_key=cfg.serpapi_key,
        queries=queries,
        jina_api_key=cfg.jina_api_key,
        lookback=cfg.lookback,
        min_reddit_comments=cfg.min_reddit_comments,
    )

    fresh = [p for p in raw if not store.seen(p.url)]
    log.info("fetched=%d fresh=%d", len(raw), len(fresh))

    scored: list[Scored] = []
    sent = 0

    if fresh:
        if not cfg.anthropic_api_key:
            msg = "ANTHROPIC_API_KEY missing; skipped scoring + drafting"
            log.error(msg)
            errors.append(msg)
        else:
            scored = score_all(cfg.anthropic_api_key, cfg.anthropic_model, fresh)
            if not scored and fresh:
                errors.append("Claude returned no scored posts (see logs for API error)")

            qualifying = [s for s in scored if s.score >= cfg.score_threshold]
            log.info("qualifying for draft: %d (threshold=%d)", len(qualifying), cfg.score_threshold)
            drafts = draft_all(cfg.anthropic_api_key, cfg.anthropic_model, qualifying)
            if qualifying and not any(drafts.values()):
                errors.append("Drafter produced no comments (see logs)")

            for s in scored:
                store.mark(s.post.url)
                if s.score >= cfg.score_threshold:
                    if deliver_lead(cfg, leads, s, drafts.get(s.post.url, "")):
                        sent += 1
                log.info("score=%d url=%s", s.score, s.post.url)

    if cfg.slack_bot_token or cfg.slack_webhook_url:  # dry runs don't count toward the day stats
        leads.record_run(len(raw), len(fresh), len(scored), sent)

    summary = RunSummary(
        fetched_total=len(raw),
        fresh=len(fresh),
        scored=len(scored),
        score_buckets=bucketize_scores([s.score for s in scored]),
        sent=sent,
        threshold=cfg.score_threshold,
        errors=errors,
    )

    # Prefer bot-token mode (lets us thread the URL list under the summary).
    # Fall back to webhook if bot_token / channel_id aren't configured.
    if cfg.slack_bot_token and cfg.slack_channel_id:
        bot_result = send_summary_via_bot(cfg.slack_bot_token, cfg.slack_channel_id, summary)
        if bot_result.get("ok") and bot_result.get("ts"):
            thread_result = post_analyzed_urls_thread(
                cfg.slack_bot_token,
                cfg.slack_channel_id,
                bot_result["ts"],
                scored,
            )
            if not thread_result.get("ok"):
                log.warning("threaded URL list failed: %s", thread_result.get("error"))
        else:
            log.warning(
                "bot-mode summary failed (%s); falling back to webhook",
                bot_result.get("error"),
            )
            send_summary_to_slack(cfg.slack_webhook_url, summary)
    else:
        send_summary_to_slack(cfg.slack_webhook_url, summary)

    return {
        "fetched": len(raw),
        "fresh": len(fresh),
        "scored": len(scored),
        "sent": sent,
        "buckets": summary.score_buckets,
    }


def lambda_handler(event: dict, context) -> dict:  # noqa: ARG001
    result = run()
    log.info("run complete: %s", result)
    return {"statusCode": 200, "body": result}

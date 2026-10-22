"""Lambda entry point + orchestrator.

Pipeline:
  1. Build Google queries for Reddit subs
  2. SerpApi search + Jina Reader enrich -> list[Post]
  3. Drop URLs already in dedup store
  4. Score each post with Claude (scout pass — score + candidate brief)
  5. For score >= threshold, draft an actual comment with Claude
     (drafter pass — brand-voice-compliant comment text)
  6. Send a Slack card per qualifying lead (brief + drafted comment)
  7. Mark all seen URLs so we don't re-score tomorrow
  8. Post a daily run-summary card to Slack (counts + score buckets)
"""

from __future__ import annotations

import logging

from .config import EXTRA_QUERIES, KEYWORD_CLUSTERS, SUBREDDITS, load_config
from .drafter import draft_all
from .models import Post
from .scorer import Scored, score_all
from .slack import (
    RunSummary,
    bucketize_scores,
    post_analyzed_urls_thread,
    send_summary_to_slack,
    send_summary_via_bot,
    send_to_slack,
)
from .sources.search import build_queries, fetch_all
from .store import build_store

log = logging.getLogger()
log.setLevel(logging.INFO)


def run() -> dict:
    cfg = load_config()
    store = build_store(cfg.dedup_backend, cfg.dynamodb_table, cfg.aws_region)

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
                    drafted = drafts.get(s.post.url, "")
                    if send_to_slack(cfg.slack_webhook_url, s, drafted_comment=drafted):
                        sent += 1
                log.info("score=%d url=%s", s.score, s.post.url)

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

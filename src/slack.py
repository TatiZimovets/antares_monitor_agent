"""Slack webhook notifier using Block Kit.

Two payload types:
  - send_to_slack(scored):      one card per high-scoring lead
  - send_summary_to_slack(run): one daily run-summary card
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone

import certifi
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .drafter import is_skip
from .scorer import Scored

# Python 3.14 on macOS doesn't ship a default trust store for urllib;
# slack_sdk uses urllib under the hood. Reuse certifi's bundle.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    fetched_total: int
    fresh: int                      # after dedup
    scored: int
    score_buckets: dict[str, int]   # {"9-10": n, "7-8": n, "5-6": n, "1-4": n}
    sent: int
    threshold: int
    errors: list[str]               # short human-readable messages


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _emoji_for_score(score: int) -> str:
    if score >= 9:
        return ":fire:"
    if score >= 7:
        return ":dart:"
    return ":grey_question:"


def _draft_section(drafted_comment: str) -> dict:
    """Render the drafter's output. Three states: missing / skipped / real comment."""
    if not drafted_comment:
        body = ":warning: _Drafter unavailable — write the comment manually._"
    elif is_skip(drafted_comment):
        body = (
            ":no_entry: _Drafter declined: brand voice cannot be honored here. "
            "Engage manually only if you can avoid the flagged framing._"
        )
    else:
        # ``` block makes copy-paste clean (preserves whitespace, no Slack auto-formatting)
        body = "*Drafted comment* _(copy-paste ready):_\n```" + _truncate(drafted_comment, 2800) + "```"
    return {"type": "section", "text": {"type": "mrkdwn", "text": body}}


def _build_blocks(s: Scored, drafted_comment: str = "") -> list[dict]:
    p = s.post
    platform_label = "Reddit"
    parts = [f"*{platform_label}*"]
    if p.platform == "reddit" and p.subreddit:
        parts.append(f"r/{p.subreddit}")
    if p.comments:
        parts.append(f"{p.comments} comments")
    if p.created_hint:
        parts.append(_truncate(p.created_hint, 40))
    context_line = "  |  ".join(parts)

    title = p.title or _truncate(p.text, 120) or p.url

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{_emoji_for_score(s.score)} Score {s.score}/10 — {platform_label}",
                "emoji": True,
            },
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_line}]},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*<{p.url}|{_truncate(title, 150)}>*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Why it matched:* {_truncate(s.reasoning, 500)}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Candidate brief:*\n>>> {_truncate(s.candidate_brief, 2000)}"},
        },
        {"type": "divider"},
        _draft_section(drafted_comment),
    ]


def send_to_slack(webhook_url: str, scored: Scored, drafted_comment: str = "") -> bool:
    if not webhook_url:
        log.warning("slack: webhook missing, skipping")
        return False
    payload = {
        "text": f"Antares lead — score {scored.score}/10 on {scored.post.platform}",
        "blocks": _build_blocks(scored, drafted_comment),
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("slack post failed for %s: %s", scored.post.url, e)
        return False


# ─── Bot-token path: lead cards with Replied / Skip buttons ─────────────────
# Clicks are handled by scripts/slack_listener.py (Socket Mode). The button
# value is the lead's url_key, which is the primary key in src/leads.py.

LEAD_ACTIONS_BLOCK_ID = "lead_actions"
LEAD_STATUS_BLOCK_ID = "lead_status"


def lead_actions_block(key: str) -> dict:
    return {
        "type": "actions",
        "block_id": LEAD_ACTIONS_BLOCK_ID,
        "elements": [
            {
                "type": "button",
                "action_id": "lead_replied",
                "text": {"type": "plain_text", "text": ":white_check_mark: Replied", "emoji": True},
                "style": "primary",
                "value": key,
            },
            {
                "type": "button",
                "action_id": "lead_skipped",
                "text": {"type": "plain_text", "text": ":fast_forward: Skip", "emoji": True},
                "value": key,
            },
        ],
    }


def lead_status_block(key: str, status: str, user_id: str, at: datetime) -> dict:
    label = ":white_check_mark: *Replied*" if status == "replied" else ":fast_forward: *Skipped*"
    return {
        "type": "section",
        "block_id": LEAD_STATUS_BLOCK_ID,
        "text": {"type": "mrkdwn", "text": f"{label} by <@{user_id}> · {at.strftime('%b %d, %H:%M')}"},
        "accessory": {
            "type": "button",
            "action_id": "lead_undo",
            "text": {"type": "plain_text", "text": "Undo"},
            "value": key,
        },
    }


def send_lead_via_bot(bot_token: str, channel: str, scored: Scored, key: str, drafted_comment: str = "") -> dict:
    """Post one lead card with Replied / Skip buttons. Returns {ok, ts, channel, error}."""
    blocks = _build_blocks(scored, drafted_comment) + [lead_actions_block(key)]
    fallback = f"Antares lead — score {scored.score}/10 on {scored.post.platform}"
    try:
        client = WebClient(token=bot_token, ssl=_SSL_CONTEXT)
        resp = client.chat_postMessage(channel=channel, text=fallback, blocks=blocks, unfurl_links=False)
        return {"ok": True, "ts": resp.get("ts"), "channel": resp.get("channel")}
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        log.error("slack lead (bot) failed for %s: %s", scored.post.url, err)
        return {"ok": False, "error": err}
    except Exception as e:
        log.error("slack lead (bot) unexpected error for %s: %s", scored.post.url, e)
        return {"ok": False, "error": str(e)}


def bucketize_scores(scores: list[int]) -> dict[str, int]:
    """Bucket a list of raw scores into the rubric tiers."""
    buckets = {"9-10": 0, "7-8": 0, "5-6": 0, "1-4": 0}
    for s in scores:
        if s >= 9:
            buckets["9-10"] += 1
        elif s >= 7:
            buckets["7-8"] += 1
        elif s >= 5:
            buckets["5-6"] += 1
        else:
            buckets["1-4"] += 1
    return buckets


def _build_summary_blocks(r: RunSummary) -> list[dict]:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fetched_line = f"*Fetched from Reddit:* {r.fetched_total}"
    pipeline_line = (
        f"*New (after dedup):* {r.fresh}"
        f"   •   *Scored:* {r.scored}"
        f"   •   *Sent:* {r.sent}  _(threshold ≥ {r.threshold})_"
    )

    b = r.score_buckets
    bucket_lines = (
        f":fire: *9-10*  (top):  `{b.get('9-10', 0)}`\n"
        f":dart: *7-8*   (good):  `{b.get('7-8', 0)}`\n"
        f":grey_question: *5-6*   (soft):  `{b.get('5-6', 0)}`\n"
        f":wastebasket: *1-4*   (skip):  `{b.get('1-4', 0)}`"
    )

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":bar_chart: Antares Daily Scan — {date_str}",
                "emoji": True,
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": fetched_line}},
        {"type": "section", "text": {"type": "mrkdwn", "text": pipeline_line}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Score breakdown:*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": bucket_lines}},
    ]

    if r.errors:
        err_text = "\n".join(f"• {_truncate(e, 300)}" for e in r.errors[:5])
        blocks.append({"type": "divider"})
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f":warning: *Issues:*\n{err_text}"}}
        )

    return blocks


def send_summary_to_slack(webhook_url: str, summary: RunSummary) -> bool:
    """Webhook-based summary posting. Returns True/False; can't return a ts so
    can't thread a reply under it. Kept for legacy / no-bot-token deployments."""
    if not webhook_url:
        log.warning("slack summary: webhook missing, skipping")
        return False
    payload = {
        "text": (
            f"Antares daily scan — fetched {summary.fetched_total} posts, "
            f"sent {summary.sent} leads ≥ {summary.threshold}"
        ),
        "blocks": _build_summary_blocks(summary),
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("slack summary post failed: %s", e)
        return False


# ─── Bot-token path: summary + threaded URL list ────────────────────────────


def _format_url_list_blocks(scored_list: list[Scored]) -> list[dict]:
    """Group analysed URLs by score bucket and return Block Kit sections for
    a threaded reply. Splits a bucket across multiple sections if its text
    exceeds Slack's ~3000-char per-block limit."""
    by_bucket: dict[str, list[Scored]] = {"9-10": [], "7-8": [], "5-6": [], "1-4": []}
    for s in scored_list:
        if s.score >= 9:
            by_bucket["9-10"].append(s)
        elif s.score >= 7:
            by_bucket["7-8"].append(s)
        elif s.score >= 5:
            by_bucket["5-6"].append(s)
        else:
            by_bucket["1-4"].append(s)

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*All analysed URLs — {len(scored_list)}:*"},
        }
    ]

    bucket_meta = [
        ("9-10", ":fire:", "top"),
        ("7-8", ":dart:", "good"),
        ("5-6", ":grey_question:", "soft"),
        ("1-4", ":wastebasket:", "skip"),
    ]
    for key, emoji, label in bucket_meta:
        items = by_bucket[key]
        if not items:
            continue
        header = f"{emoji} *{key}* — {label} ({len(items)})"
        # Sort descending within bucket so high scores show first.
        rows = [
            f"   • {s.score}/10 — <{s.post.url}|{_truncate(s.post.url, 100)}>"
            for s in sorted(items, key=lambda x: -x.score)
        ]
        # Pack into one section per bucket, splitting if it exceeds Slack's limit.
        chunk = header
        for row in rows:
            candidate = chunk + "\n" + row
            if len(candidate) > 2800:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
                chunk = row
            else:
                chunk = candidate
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    return blocks


def send_summary_via_bot(bot_token: str, channel: str, summary: RunSummary) -> dict:
    """Post the daily summary card to a channel via Bot Token. Returns
    {ok, ts, channel, error}. The ts lets us thread a reply under it.

    Bot must be a member of `channel` — invite with `/invite @<bot>` once
    or this call returns `not_in_channel`.
    """
    if not bot_token or not channel:
        return {"ok": False, "error": "missing bot token or channel id"}
    blocks = _build_summary_blocks(summary)
    fallback = (
        f"Antares daily scan — fetched {summary.fetched_total} posts, "
        f"sent {summary.sent} leads ≥ {summary.threshold}"
    )
    try:
        client = WebClient(token=bot_token, ssl=_SSL_CONTEXT)
        resp = client.chat_postMessage(channel=channel, text=fallback, blocks=blocks)
        return {"ok": True, "ts": resp.get("ts"), "channel": resp.get("channel")}
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        log.error("slack summary (bot) failed: %s", err)
        return {"ok": False, "error": err}
    except Exception as e:
        log.error("slack summary (bot) unexpected error: %s", e)
        return {"ok": False, "error": str(e)}


def post_analyzed_urls_thread(
    bot_token: str,
    channel: str,
    thread_ts: str,
    scored_list: list[Scored],
) -> dict:
    """Post the full analysed-URL list as a threaded reply under the summary."""
    if not scored_list:
        return {"ok": True, "skipped": "no scored posts"}
    if not bot_token or not channel or not thread_ts:
        return {"ok": False, "error": "missing bot token, channel id, or thread_ts"}
    blocks = _format_url_list_blocks(scored_list)
    fallback = f"All analysed URLs — {len(scored_list)}"
    try:
        client = WebClient(token=bot_token, ssl=_SSL_CONTEXT)
        resp = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=fallback,
            blocks=blocks,
        )
        return {"ok": True, "ts": resp.get("ts")}
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        log.error("slack threaded URL list failed: %s", err)
        return {"ok": False, "error": err}
    except Exception as e:
        log.error("slack threaded URL list unexpected error: %s", e)
        return {"ok": False, "error": str(e)}


# ─── Evening stats card ─────────────────────────────────────────────────────


def _build_daily_stats_blocks(day: str, today: dict, week: dict) -> list[dict]:
    def funnel(s: dict) -> str:
        return (
            f"*Found on Reddit:* {s['found']}   •   *New:* {s['fresh']}   •   *Scored:* {s['scored']}\n"
            f"*Leads sent:* {s['sent']}   •   *Replied:* {s['replied']}   •   *Skipped:* {s['skipped']}"
        )

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f":chart_with_upwards_trend: Antares day summary — {day}", "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": funnel(today)}},
    ]
    if today["runs"] == 0:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": ":warning: No scan ran today."}]}
        )
    blocks += [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":hourglass_flowing_sand: *Still waiting for Replied / Skip:* {today['open']}"},
        },
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Last 7 days*\n{funnel(week)}"}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Replies to your comments: coming once Reddit API access is approved."}
            ],
        },
    ]
    return blocks


def send_daily_stats(bot_token: str, channel: str, day: str, today: dict, week: dict) -> dict:
    fallback = (
        f"Antares day summary — {day}: found {today['found']}, sent {today['sent']}, "
        f"replied {today['replied']}, skipped {today['skipped']}"
    )
    try:
        client = WebClient(token=bot_token, ssl=_SSL_CONTEXT)
        resp = client.chat_postMessage(
            channel=channel, text=fallback, blocks=_build_daily_stats_blocks(day, today, week)
        )
        return {"ok": True, "ts": resp.get("ts")}
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        log.error("slack daily stats failed: %s", err)
        return {"ok": False, "error": err}
    except Exception as e:
        log.error("slack daily stats unexpected error: %s", e)
        return {"ok": False, "error": str(e)}

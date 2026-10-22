"""Comment drafter: turns a high-scoring lead into a brand-voice-compliant comment.

Run as a second pass after scoring. The scout (scorer.py) decides which posts
deserve attention; the drafter writes the actual reply text using the brand-voice
prompt at prompts/comment_drafter.md.

Output: a string, ready to copy-paste, OR the literal skip-token string when the
model determines the post can't be answered without breaking voice.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .scorer import Scored

log = logging.getLogger(__name__)


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "comment_drafter.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

SKIP_TOKEN = "[skip — cannot write within brand voice]"


def is_skip(text: str) -> bool:
    """Match either the canonical em-dash skip token or a hyphenated variant."""
    norm = text.strip().lower().replace("—", "-").replace("–", "-")
    return norm.startswith("[skip")


def _format_input(scored: Scored) -> str:
    p = scored.post
    parts = [
        f"Platform: {p.platform}",
        f"URL: {p.url}",
    ]
    if p.platform == "reddit" and p.subreddit:
        parts.append(f"Subreddit: r/{p.subreddit}")
    if p.comments:
        parts.append(f"Comment count (parsed): {p.comments}")
    if p.created_hint:
        parts.append(f"Posted: {p.created_hint}")
    if p.title:
        parts.append(f"Title: {p.title}")
    parts += [
        "",
        f"Scout score: {scored.score}/10",
        f"Scout reasoning: {scored.reasoning}",
        f"Candidate brief: {scored.candidate_brief}",
        "",
        "--- post body ---",
        p.text,
        "--- end post ---",
    ]
    return "\n".join(parts)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def draft_comment(client: Anthropic, model: str, scored: Scored) -> str:
    messages = [{"role": "user", "content": _format_input(scored)}]
    text = _complete(client, model, messages)
    # The model tends to overrun the length rule; one tightening pass fixes it.
    words = len(text.split())
    if words > MAX_WORDS and not is_skip(text):
        messages += [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    f"That's {words} words. Cut it to under 100 by dropping a whole point, "
                    "not by compressing sentences. Keep the voice. Output only the reply."
                ),
            },
        ]
        text = _complete(client, model, messages)
    return _strip_ai_tells(text)


MAX_WORDS = 110


def _complete(client: Anthropic, model: str, messages: list[dict]) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages,
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


_EMPHASIS_RE = re.compile(r"\*{1,2}([^*\n]+)\*{1,2}")


def _strip_ai_tells(text: str) -> str:
    """Backstop for typography the prompt bans but the model still slips in:
    em dashes, arrows and markdown emphasis read as AI-written on Reddit."""
    if is_skip(text):
        return text
    text = text.replace(" — ", ", ").replace("—", ", ")
    text = text.replace(" → ", " to ").replace("→", " to ")
    return _EMPHASIS_RE.sub(r"\1", text)


def draft_all(api_key: str, model: str, leads: list[Scored]) -> dict[str, str]:
    """Returns {post_url: drafted_text}. Empty string on failure."""
    if not leads:
        return {}
    client = Anthropic(api_key=api_key)
    out: dict[str, str] = {}
    for lead in leads:
        try:
            out[lead.post.url] = draft_comment(client, model, lead)
        except Exception as e:
            log.error("draft failed for %s: %s", lead.post.url, e)
            out[lead.post.url] = ""
    return out

"""Claude-based scoring + reply drafting for social posts.

Uses prompt caching on the long system prompt so repeat runs are ~90% cheaper.
Output is strict JSON: {score, reasoning, candidate_brief}.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import Post

log = logging.getLogger(__name__)


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "scoring_system.md"


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# Cached at import so one run reads the file once.
SYSTEM_PROMPT = _load_system_prompt()


@dataclass
class Scored:
    post: Post
    score: int
    reasoning: str
    candidate_brief: str  # handoff for the downstream commenter, NOT the comment itself


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(m.group(0))


def _format_post(post: Post) -> str:
    header = f"Platform: {post.platform}\nURL: {post.url}\n"
    if post.platform == "reddit":
        header += f"Subreddit: r/{post.subreddit}\nComments (parsed): {post.comments}\n"
    if post.created_hint:
        header += f"Posted: {post.created_hint}\n"
    if post.title:
        header += f"Title: {post.title}\n"
    header += f"Matched query: {', '.join(post.matched_keywords)}\n\n"
    return header + f"---\n{post.text}\n---"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def score_post(client: Anthropic, model: str, post: Post) -> Scored:
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": _format_post(post)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        data = _extract_json(text)
    except Exception as e:
        log.warning("failed to parse score for %s: %s", post.url, e)
        return Scored(post=post, score=0, reasoning=f"parse_error: {e}", candidate_brief="")

    return Scored(
        post=post,
        score=int(data.get("score", 0)),
        reasoning=str(data.get("reasoning", "")),
        candidate_brief=str(data.get("candidate_brief", "")),
    )


def score_all(api_key: str, model: str, posts: list[Post]) -> list[Scored]:
    client = Anthropic(api_key=api_key)
    out: list[Scored] = []
    for p in posts:
        try:
            out.append(score_post(client, model, p))
        except Exception as e:
            log.error("scoring failed for %s: %s", p.url, e)
    return out

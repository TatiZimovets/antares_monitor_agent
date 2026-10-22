"""Reddit discovery via Google (SerpApi) + full-text via Jina Reader.

Why Google instead of the Reddit API:
  - Reddit's API now requires manual approval that typically rejects sales bots.
  - Google already indexes Reddit, and SerpApi gives us fresh, filtered results.
  - Jina Reader (r.jina.ai/<url>) renders the actual post content without
    auth walls, ads, or rate limits from the source sites.

One API key (SerpApi) + one optional key (Jina) covers the whole pipeline.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import Post

log = logging.getLogger(__name__)


SERPAPI_URL = "https://serpapi.com/search.json"
JINA_URL = "https://r.jina.ai/{url}"

# "2 days ago", "3 hours ago" — relative dates in Google snippets mean recent.
RECENT_RE = re.compile(r"\b(\d+)\s+(minute|hour|day|week)s?\s+ago\b", re.I)

# Reddit post URLs: /r/<sub>/comments/<id>/<slug>/
REDDIT_POST_RE = re.compile(r"reddit\.com/r/([^/]+)/comments/[^/]+/", re.I)

# Parse "123 comments" out of Jina-rendered Reddit posts.
REDDIT_COMMENTS_RE = re.compile(r"(\d+)\s+comments?\b", re.I)

# Google Translate URL variants — `?tl=fr`, `?tl=es`, etc. These pull in
# non-English posts where "tableau" is just the French/Italian word for
# "dashboard/chart" rather than the Tableau product.
TRANSLATED_URL_RE = re.compile(r"[?&]tl=", re.I)


# Reddit sometimes serves Jina a "blocked by network security" page instead
# of the thread. Treat that as no content and fall back to Google's snippet.
_BLOCKED_PAGE_RE = re.compile(
    r"Target URL returned error 40[39]|You've been blocked by network security", re.I
)

SNIPPET_ONLY_NOTE = "[Full thread text unavailable — Google title + snippet only]"


def _is_translated_url(url: str) -> bool:
    """Skip Google Translate variants. `tableau` in French/Italian/Spanish is
    'dashboard/chart' — these posts pollute results with language collisions."""
    return bool(TRANSLATED_URL_RE.search(url))

# Topic guard, applied to the fetched post text. A post is kept only if it
# names at least one BI platform AND carries a migration/planning signal.
# Google matches on sidebars and related-post lists too, so this catches
# results that only *mention* a BI tool somewhere on the page.
_BI_PLATFORM_WORDS = [
    r"tableau", r"power\s*bi", r"pbi", r"fabric",
    r"ai/bi", r"aibi", r"lakeview", r"databricks", r"sigma",
    r"qlik\w*", r"cognos", r"microstrategy", r"business\s*objects",
    r"ssrs", r"looker", r"spotfire", r"domo", r"sas",
    r"dashboards?", r"bi",
]
_BI_PLATFORM_RE = re.compile(r"\b(?:" + "|".join(_BI_PLATFORM_WORDS) + r")\b", re.I)

_MIGRATION_SIGNAL_WORDS = [
    r"migrat\w*",                       # migrate, migration, migrating
    r"convert\w*",                      # convert, conversion, converter
    r"rebuil\w*", r"recreat\w*",       # rebuild, rebuilt, recreate
    r"switch(?:ing|ed)?\s+(?:from|to|over)",
    r"mov(?:e|ing|ed)\s+(?:from|to|off)",
    r"replac\w*",                       # replace, replacing, replacement
    r"sunset\w*", r"decommission\w*",
    r"lod", r"dax",
    r"parity",
    r"complexity", r"effort", r"estimat\w*", r"inventory",
]
_MIGRATION_SIGNAL_RE = re.compile(r"\b(?:" + "|".join(_MIGRATION_SIGNAL_WORDS) + r")\b", re.I)


@dataclass
class SerpHit:
    url: str
    title: str
    snippet: str
    date_hint: str
    query: str


def _platform_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "reddit.com" in host:
        return "reddit"
    return "other"


def _subreddit_of(url: str) -> str:
    m = REDDIT_POST_RE.search(url)
    return m.group(1) if m else ""


def _is_recent(date_hint: str, max_days: int = 7) -> bool:
    """Prune obviously stale posts. Trust Google's tbs=qdr:* filter by default.

    Only reject when we see an explicit "N days/weeks ago" that's outside the
    window — SerpApi's `date` field is usually None for Reddit, so
    we can't require a positive signal without losing every result.
    """
    if not date_hint:
        return True
    m = RECENT_RE.search(date_hint)
    if not m:
        return True  # can't parse -> trust server-side tbs filter
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit in ("minute", "hour"):
        return True
    if unit == "day":
        return n <= max_days
    if unit == "week":
        return n * 7 <= max_days
    return True


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _serp_search(api_key: str, query: str, tbs: str) -> list[SerpHit]:
    params = {
        "engine": "google",
        "q": query,
        "tbs": tbs,
        "num": 20,
        "api_key": api_key,
    }
    r = requests.get(SERPAPI_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    out: list[SerpHit] = []
    for item in data.get("organic_results", []):
        url = item.get("link", "")
        if _platform_of(url) == "other":
            continue
        out.append(
            SerpHit(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                date_hint=item.get("date", "") or item.get("snippet", ""),
                query=query,
            )
        )
    return out


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=8))
def _jina_fetch(url: str, api_key: str = "") -> str:
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    r = requests.get(JINA_URL.format(url=url), headers=headers, timeout=45)
    if r.status_code == 402 and api_key:
        # Key out of balance — unauthenticated reads still work (rate-limited).
        headers.pop("Authorization")
        r = requests.get(JINA_URL.format(url=url), headers=headers, timeout=45)
    r.raise_for_status()
    return r.text


def _parse_reddit_comments(text: str) -> int:
    """Best-effort extraction of comment count from Jina-rendered Reddit page."""
    m = REDDIT_COMMENTS_RE.search(text[:2000])
    return int(m.group(1)) if m else 0


def _qualify(kw: str) -> str:
    """Exact-phrase a keyword, or pass it through if it's already Google syntax.

    "tableau to power bi"                 -> '"tableau to power bi"'
    '"migration" "numbers don't match"'   -> '("migration" "numbers don't match")'
    """
    kw = kw.strip()
    if kw.startswith("("):
        return kw
    if '"' in kw:
        return f"({kw})"   # keep multi-phrase terms together inside the OR
    return f'"{kw}"'


def _or_group(keywords: list[str]) -> str:
    """OR all keywords in a chunk together so we burn ONE query, not N."""
    return "(" + " OR ".join(_qualify(k) for k in keywords) + ")"


def _split_evenly(seq: list[str], max_size: int) -> list[list[str]]:
    """Split into roughly-equal chunks, each <= max_size items.

    For 7 items with max=5  -> [4, 3]      (not [5, 2])
    For 11 items with max=5 -> [4, 4, 3]   (not [5, 5, 1])
    Even distribution avoids one-keyword chunks burning a full SerpApi query.
    """
    n = len(seq)
    if n == 0:
        return []
    if n <= max_size:
        return [list(seq)]
    num_chunks = (n + max_size - 1) // max_size  # ceil(n / max_size)
    base, extras = divmod(n, num_chunks)
    chunks: list[list[str]] = []
    i = 0
    for k in range(num_chunks):
        size = base + (1 if k < extras else 0)
        chunks.append(list(seq[i:i + size]))
        i += size
    return chunks


def build_queries(
    subreddits: Iterable[str],
    keyword_clusters: dict[str, list[str]],
    extra_queries: Iterable[str] = (),
    max_or_terms: int = 5,
) -> list[str]:
    """Build Google queries targeting Reddit subs.

    Strategy: each cluster is split into one or more sub-groups of at most
    ``max_or_terms`` keywords (Google's fuzzy-fill kicks in on long OR
    queries — 10+ terms causes off-topic fills). Each sub-group gets one
    SerpApi query per subreddit, or one site-wide Reddit query when no
    subreddits are configured.

    extra_queries are raw Google search strings appended verbatim.
    """
    queries: list[str] = []

    for kws in keyword_clusters.values():
        kws = [k for k in kws if k.strip()]
        if not kws:
            continue
        for chunk in _split_evenly(kws, max_or_terms):
            group = _or_group(chunk)
            if subreddits:
                for sub in subreddits:
                    queries.append(f"site:reddit.com/r/{sub} {group}")
            else:
                queries.append(f"site:reddit.com {group}")

    queries.extend(q for q in extra_queries if q.strip())
    return queries


def fetch_all(
    serpapi_key: str,
    queries: Iterable[str],
    jina_api_key: str = "",
    lookback: str = "qdr:w",
    enrich: bool = True,
    min_reddit_comments: int = 0,
    sleep_between_enrich: float = 0.5,
) -> list[Post]:
    """Run SerpApi searches, dedupe URLs, enrich with Jina, return Posts."""
    if not serpapi_key:
        log.warning("search: SERPAPI_KEY missing, skipping")
        return []

    seen: set[str] = set()
    posts: list[Post] = []

    for q in queries:
        try:
            hits = _serp_search(serpapi_key, q, tbs=lookback)
        except Exception as e:
            log.warning("serp failed q=%r: %s", q, e)
            continue

        for h in hits:
            if h.url in seen:
                continue
            if _is_translated_url(h.url):
                log.info("skip: translated URL — %s", h.url)
                seen.add(h.url)
                continue
            if not _is_recent(h.date_hint):
                log.debug("skip stale: %s (%s)", h.url, h.date_hint)
                continue
            seen.add(h.url)

            body = h.snippet

            if enrich:
                try:
                    body = _jina_fetch(h.url, jina_api_key) or h.snippet
                    if _BLOCKED_PAGE_RE.search(body[:1500]):
                        log.info("jina blocked by reddit, using snippet — %s", h.url)
                        body = f"{SNIPPET_ONLY_NOTE}\n{h.title}\n{h.snippet}"
                    time.sleep(sleep_between_enrich)
                except Exception as e:
                    log.warning("jina failed %s: %s", h.url, e)

            # Topic guard: must name a BI platform AND carry a migration
            # signal — otherwise Google matched on sidebar / related posts.
            text = f"{h.title}\n{body}"
            if not _BI_PLATFORM_RE.search(text):
                log.info("skip: no BI platform in body — %s", h.url)
                continue
            if not _MIGRATION_SIGNAL_RE.search(text):
                log.info("skip: no migration signal in body — %s", h.url)
                continue

            comments = _parse_reddit_comments(body)
            if min_reddit_comments and comments < min_reddit_comments:
                log.debug("skip low-engagement reddit: %s (%d comments)", h.url, comments)
                continue

            posts.append(
                Post(
                    platform="reddit",
                    url=h.url,
                    title=h.title,
                    text=body[:8000],
                    subreddit=_subreddit_of(h.url),
                    comments=comments,
                    created_hint=h.date_hint,
                    matched_keywords=[h.query],
                )
            )

    log.info("search: %d posts after filters", len(posts))
    return posts

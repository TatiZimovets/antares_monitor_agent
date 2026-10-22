"""Run the pipeline locally without Lambda.

Modes:
    python scripts/local_run.py                # full pipeline, sends Slack
    python scripts/local_run.py --dry-run      # full pipeline, no Slack
    python scripts/local_run.py --smoke        # 2 queries only, prints results
    python scripts/local_run.py --preview      # full fetch, prints URLs,
                                               # caches to data/last_fetch.json,
                                               # NO score / NO drafter / NO Slack
    python scripts/local_run.py --from-cache   # score+draft+Slack from cached
                                               # fetch (zero new SerpApi calls)

Requires .env with SERPAPI_KEY + ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "last_fetch.json"


def _save_cache(posts) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps([asdict(p) for p in posts], indent=2), encoding="utf-8")


def _load_cache():
    from src.models import Post
    raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return [Post(**item) for item in raw]


def _print_url_table(posts, header: str) -> None:
    print(f"\n=== {header} ({len(posts)}) ===")
    for i, p in enumerate(posts, 1):
        title = (p.title or "")[:90]
        meta = []
        if p.platform == "reddit" and p.subreddit:
            meta.append(f"r/{p.subreddit}")
        if p.comments:
            meta.append(f"{p.comments} cmts")
        if p.created_hint:
            meta.append((p.created_hint[:30] + "…") if len(p.created_hint) > 30 else p.created_hint)
        meta_s = "  ·  ".join(meta) if meta else ""
        print(f"  {i:>3}. {p.url}")
        if title:
            print(f"       {title}")
        if meta_s:
            print(f"       {meta_s}")


def _do_preview(cfg) -> int:
    from src.config import EXTRA_QUERIES, KEYWORD_CLUSTERS, SUBREDDITS
    from src.sources.search import build_queries, fetch_all

    queries = build_queries(SUBREDDITS, KEYWORD_CLUSTERS, EXTRA_QUERIES, max_or_terms=cfg.max_or_terms)
    print(f"[preview] running {len(queries)} SerpApi queries (max {cfg.max_or_terms} OR terms each)…")

    posts = fetch_all(
        serpapi_key=cfg.serpapi_key,
        queries=queries,
        jina_api_key=cfg.jina_api_key,
        lookback=cfg.lookback,
        min_reddit_comments=cfg.min_reddit_comments,
    )

    print(f"\n[preview] post-filter total={len(posts)}")
    print("[preview] (filter requires a BI platform AND a migration signal in the post)")

    _print_url_table(posts, "Reddit candidates")

    _save_cache(posts)
    print(f"\n[preview] cached {len(posts)} posts to {CACHE_PATH.relative_to(Path.cwd())}")
    print("[preview] approve, then run:  ./.venv/bin/python scripts/local_run.py --from-cache")
    return 0


def _do_from_cache(cfg) -> int:
    from src.drafter import draft_all
    from src.scorer import score_all
    from src.slack import RunSummary, bucketize_scores, send_summary_to_slack, send_to_slack
    from src.store import build_store

    if not CACHE_PATH.exists():
        print(f"[from-cache] no cache at {CACHE_PATH} — run --preview first")
        return 1

    posts = _load_cache()
    print(f"[from-cache] loaded {len(posts)} posts from cache")

    store = build_store(cfg.dedup_backend, cfg.dynamodb_table, cfg.aws_region)
    fresh = [p for p in posts if not store.seen(p.url)]
    print(f"[from-cache] fresh after dedup: {len(fresh)}")

    if not fresh:
        return 0

    scored = score_all(cfg.anthropic_api_key, cfg.anthropic_model, fresh)
    qualifying = [s for s in scored if s.score >= cfg.score_threshold]
    drafts = draft_all(cfg.anthropic_api_key, cfg.anthropic_model, qualifying) if qualifying else {}

    sent = 0
    for s in scored:
        store.mark(s.post.url)
        if s.score >= cfg.score_threshold:
            if send_to_slack(cfg.slack_webhook_url, s, drafted_comment=drafts.get(s.post.url, "")):
                sent += 1
        print(f"  score={s.score}/10  url={s.post.url}")

    summary = RunSummary(
        fetched_total=len(posts),
        fresh=len(fresh), scored=len(scored),
        score_buckets=bucketize_scores([s.score for s in scored]),
        sent=sent, threshold=cfg.score_threshold, errors=[],
    )
    send_summary_to_slack(cfg.slack_webhook_url, summary)
    print(f"\n[from-cache] sent={sent} buckets={summary.score_buckets}")
    return 0


def _do_smoke(cfg) -> int:
    from src.drafter import draft_all
    from src.models import Post
    from src.scorer import score_all
    from src.sources.search import fetch_all
    from src.store import build_store

    smoke_queries = [
        'site:reddit.com/r/PowerBI "tableau" "migration"',
        'site:reddit.com "BI migration"',
    ]
    print(f"[smoke] running {len(smoke_queries)} queries")
    store = build_store("local")
    raw: list[Post] = fetch_all(
        serpapi_key=cfg.serpapi_key,
        queries=smoke_queries,
        jina_api_key=cfg.jina_api_key,
        lookback=cfg.lookback,
        min_reddit_comments=0,
    )

    print(f"[smoke] fetched {len(raw)}")

    fresh = [p for p in raw if not store.seen(p.url)]
    print(f"[smoke] fresh   {len(fresh)}")

    if not fresh:
        print("[smoke] no fresh posts; widen LOOKBACK to qdr:m and try again")
        return 0

    sample = fresh[:4]
    print(f"[smoke] scoring {len(sample)} posts…")
    scored = score_all(cfg.anthropic_api_key, cfg.anthropic_model, sample)

    qualifying = [s for s in scored if s.score >= cfg.score_threshold]
    drafts: dict[str, str] = {}
    if qualifying:
        print(f"[smoke] drafting for {len(qualifying)} qualifying post(s)…")
        drafts = draft_all(cfg.anthropic_api_key, cfg.anthropic_model, qualifying)

    for s in scored:
        store.mark(s.post.url)
        print("---")
        print(f"url:      {s.post.url}")
        print(f"score:    {s.score}/10")
        print(f"reason:   {s.reasoning}")
        print(f"brief:    {s.candidate_brief[:400]}{'…' if len(s.candidate_brief) > 400 else ''}")
        if s.post.url in drafts:
            d = drafts[s.post.url] or "(drafter failed)"
            print(f"draft:    {d[:600]}{'…' if len(d) > 600 else ''}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="full pipeline, no Slack")
    parser.add_argument("--smoke", action="store_true", help="2 queries, no Slack")
    parser.add_argument("--preview", action="store_true",
                        help="full fetch + filter + cache, NO score/draft/Slack")
    parser.add_argument("--from-cache", action="store_true",
                        help="score+draft+Slack from cached fetch (no SerpApi)")
    args = parser.parse_args()

    os.environ.setdefault("DEDUP_BACKEND", "local")
    if args.dry_run or args.smoke or args.preview:
        os.environ["SLACK_WEBHOOK_URL"] = ""
        os.environ["SLACK_BOT_TOKEN"] = ""   # else handler posts the summary via bot

    from src import config as cfg_mod
    cfg = cfg_mod.load_config()

    if args.preview:
        return _do_preview(cfg)
    if args.from_cache:
        return _do_from_cache(cfg)
    if args.smoke:
        return _do_smoke(cfg)

    from src.handler import run
    result = run()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

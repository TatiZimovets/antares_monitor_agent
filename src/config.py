"""Load config from .env for local runs, or AWS Secrets Manager in Lambda.

Keyword/subreddit lists live in config/keywords.yaml so non-devs can tune
them without touching Python.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEYWORDS_YAML = REPO_ROOT / "config" / "keywords.yaml"


def _resolve_keywords_path() -> Path:
    """Pick the keywords file: KEYWORDS_FILE env var (relative to repo root)
    or the default config/keywords.yaml."""
    override = os.environ.get("KEYWORDS_FILE", "").strip()
    if not override:
        return DEFAULT_KEYWORDS_YAML
    p = Path(override)
    return p if p.is_absolute() else REPO_ROOT / p


def _load_keywords_yaml(path: Path | None = None) -> dict:
    """Load keyword/subreddit config. Path override is for tests."""
    target = path or _resolve_keywords_path()
    with target.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # normalize shape
    return {
        "subreddits": list(data.get("subreddits") or []),
        "keyword_clusters": dict(data.get("keyword_clusters") or {}),
        "extra_queries": list(data.get("extra_queries") or []),
    }


_YAML = _load_keywords_yaml()
SUBREDDITS: list[str] = _YAML["subreddits"]
KEYWORD_CLUSTERS: dict[str, list[str]] = _YAML["keyword_clusters"]
EXTRA_QUERIES: list[str] = _YAML["extra_queries"]


@dataclass
class Config:
    anthropic_api_key: str
    anthropic_model: str
    slack_webhook_url: str
    slack_bot_token: str
    slack_channel_id: str
    serpapi_key: str
    jina_api_key: str
    score_threshold: int
    lookback: str                # Google tbs window: qdr:d | qdr:w
    min_reddit_comments: int
    max_or_terms: int            # cap on OR'd keywords per SerpApi query
    dedup_backend: str
    dynamodb_table: str
    aws_region: str


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _load_secrets_manager(secret_id: str) -> dict:
    """Fetch a JSON secret from AWS Secrets Manager. Returns {} on failure."""
    try:
        import boto3
        client = boto3.client("secretsmanager", region_name=_get("AWS_REGION", "us-east-1"))
        resp = client.get_secret_value(SecretId=secret_id)
        return json.loads(resp["SecretString"])
    except Exception:
        return {}


def load_config() -> Config:
    secret_id = _get("SECRETS_MANAGER_ID")
    if secret_id:
        for k, v in _load_secrets_manager(secret_id).items():
            os.environ.setdefault(k, v)

    return Config(
        anthropic_api_key=_get("ANTHROPIC_API_KEY"),
        anthropic_model=_get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        slack_webhook_url=_get("SLACK_WEBHOOK_URL"),
        slack_bot_token=_get("SLACK_BOT_TOKEN"),
        slack_channel_id=_get("SLACK_CHANNEL_ID"),
        serpapi_key=_get("SERPAPI_KEY"),
        jina_api_key=_get("JINA_API_KEY"),
        score_threshold=int(_get("SCORE_THRESHOLD", "7")),
        lookback=_get("LOOKBACK", "qdr:w"),
        min_reddit_comments=int(_get("MIN_REDDIT_COMMENTS", "3")),
        max_or_terms=int(_get("MAX_OR_TERMS_PER_QUERY", "5")),
        dedup_backend=_get("DEDUP_BACKEND", "local"),
        dynamodb_table=_get("DYNAMODB_TABLE", "antares-seen-urls"),
        aws_region=_get("AWS_REGION", "us-east-1"),
    )

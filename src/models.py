"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Post:
    platform: str          # "reddit"
    url: str
    title: str
    text: str
    author: str = ""
    subreddit: str = ""
    comments: int = 0      # parsed best-effort; 0 if unknown
    created_hint: str = "" # e.g. "2 days ago" from Google snippet
    matched_keywords: list[str] = field(default_factory=list)

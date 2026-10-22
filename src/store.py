"""URL dedup store. Local SQLite for dev, DynamoDB for Lambda."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Protocol


def url_key(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


class Store(Protocol):
    def seen(self, url: str) -> bool: ...
    def mark(self, url: str) -> None: ...


class SQLiteStore:
    def __init__(self, path: str = "data/seen.sqlite"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_urls ("
            "  key TEXT PRIMARY KEY,"
            "  url TEXT NOT NULL,"
            "  ts  INTEGER NOT NULL"
            ")"
        )
        self.conn.commit()

    def seen(self, url: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen_urls WHERE key = ?", (url_key(url),))
        return cur.fetchone() is not None

    def mark(self, url: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_urls (key, url, ts) VALUES (?, ?, ?)",
            (url_key(url), url, int(time.time())),
        )
        self.conn.commit()


class DynamoDBStore:
    def __init__(self, table: str, region: str, ttl_days: int = 30):
        import boto3
        self.table = boto3.resource("dynamodb", region_name=region).Table(table)
        self.ttl_days = ttl_days

    def seen(self, url: str) -> bool:
        resp = self.table.get_item(Key={"url_key": url_key(url)})
        return "Item" in resp

    def mark(self, url: str) -> None:
        self.table.put_item(
            Item={
                "url_key": url_key(url),
                "url": url,
                "ts": int(time.time()),
                "ttl": int(time.time()) + self.ttl_days * 86400,
            }
        )


def build_store(backend: str, table: str = "", region: str = "") -> Store:
    if backend == "dynamodb":
        return DynamoDBStore(table=table, region=region)
    return SQLiteStore()

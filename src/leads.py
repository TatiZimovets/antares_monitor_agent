"""Lead tracking for the Slack Replied / Skip buttons and the evening stats.

Lives in the same SQLite file as the dedup store (data/seen.sqlite). Both the
9am run and the Socket Mode listener write here, so every connection uses a
busy timeout. When the listener moves to Lambda this needs a DynamoDB twin,
like store.py.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .scorer import Scored
from .store import url_key

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "seen.sqlite"

STATUSES = ("new", "replied", "skipped")


class LeadsDB:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS leads ("
            "  key       TEXT PRIMARY KEY,"
            "  url       TEXT NOT NULL,"
            "  title     TEXT,"
            "  subreddit TEXT,"
            "  score     INTEGER,"
            "  found_at  INTEGER NOT NULL,"
            "  status    TEXT NOT NULL DEFAULT 'new',"
            "  status_at INTEGER,"
            "  status_by TEXT,"
            "  channel   TEXT,"
            "  ts        TEXT"
            ");"
            "CREATE TABLE IF NOT EXISTS runs ("
            "  ts      INTEGER NOT NULL,"
            "  fetched INTEGER NOT NULL,"
            "  fresh   INTEGER NOT NULL,"
            "  scored  INTEGER NOT NULL,"
            "  sent    INTEGER NOT NULL"
            ");"
        )
        self.conn.commit()

    def add_lead(self, scored: Scored, channel: str, ts: str) -> str:
        p = scored.post
        key = url_key(p.url)
        self.conn.execute(
            "INSERT OR REPLACE INTO leads (key, url, title, subreddit, score, found_at, channel, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key, p.url, p.title, p.subreddit, scored.score, int(time.time()), channel, ts),
        )
        self.conn.commit()
        return key

    def set_status(self, key: str, status: str, by: str = "") -> dict | None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        now = int(time.time()) if status != "new" else None
        self.conn.execute(
            "UPDATE leads SET status = ?, status_at = ?, status_by = ? WHERE key = ?",
            (status, now, by if status != "new" else None, key),
        )
        self.conn.commit()
        return self.get(key)

    def get(self, key: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM leads WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def record_run(self, fetched: int, fresh: int, scored: int, sent: int) -> None:
        self.conn.execute(
            "INSERT INTO runs (ts, fetched, fresh, scored, sent) VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), fetched, fresh, scored, sent),
        )
        self.conn.commit()

    def stats(self, start: int, end: int) -> dict:
        """Counts for the [start, end) window, in unix seconds.

        found/fresh/scored/sent come from the runs in the window; replied and
        skipped count button clicks made in the window, whatever day the lead
        was found. `open` is every lead still waiting for a decision.
        """
        run = self.conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(fetched), 0) AS found,"
            " COALESCE(SUM(fresh), 0) AS fresh, COALESCE(SUM(scored), 0) AS scored,"
            " COALESCE(SUM(sent), 0) AS sent"
            " FROM runs WHERE ts >= ? AND ts < ?",
            (start, end),
        ).fetchone()
        by_status = dict(
            self.conn.execute(
                "SELECT status, COUNT(*) FROM leads"
                " WHERE status != 'new' AND status_at >= ? AND status_at < ? GROUP BY status",
                (start, end),
            ).fetchall()
        )
        open_count = self.conn.execute("SELECT COUNT(*) FROM leads WHERE status = 'new'").fetchone()[0]
        return {
            **dict(run),
            "replied": by_status.get("replied", 0),
            "skipped": by_status.get("skipped", 0),
            "open": open_count,
        }

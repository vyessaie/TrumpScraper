"""SQLite persistence: content items, extracted mentions, and run state.

The store dedupes content by ``(source, external_id)`` so re-running the
pipeline never double-counts a post, and accumulates mentions across runs so the
daily report can look back over a configurable window.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable

from .models import Mention, RawItem, StoredItem, utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS content_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    url          TEXT DEFAULT '',
    author       TEXT DEFAULT '',
    published_at TEXT,
    text         TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    analyzed     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS mentions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id  INTEGER NOT NULL REFERENCES content_items(id),
    company     TEXT NOT NULL,
    ticker      TEXT,
    sentiment   TEXT NOT NULL,
    score       REAL NOT NULL,
    confidence  REAL NOT NULL,
    quote       TEXT DEFAULT '',
    rationale   TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mentions_created_at ON mentions(created_at);
CREATE INDEX IF NOT EXISTS idx_content_analyzed ON content_items(analyzed);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- content ---
    def add_item(self, item: RawItem) -> int | None:
        """Insert a content item. Returns the new row id, or None if duplicate."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO content_items
               (source, external_id, url, author, published_at, text, fetched_at, analyzed)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                item.source,
                item.external_id,
                item.url,
                item.author,
                item.published_at,
                item.text,
                utcnow_iso(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def add_items(self, items: Iterable[RawItem]) -> list[int]:
        new_ids: list[int] = []
        for item in items:
            rid = self.add_item(item)
            if rid is not None:
                new_ids.append(rid)
        return new_ids

    def get_unanalyzed(self, limit: int | None = None) -> list[StoredItem]:
        sql = "SELECT * FROM content_items WHERE analyzed = 0 ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_row_to_item(r) for r in self.conn.execute(sql)]

    def mark_analyzed(self, content_id: int) -> None:
        self.conn.execute(
            "UPDATE content_items SET analyzed = 1 WHERE id = ?", (content_id,)
        )
        self.conn.commit()

    # --- mentions ---
    def add_mentions(self, content_id: int, mentions: Iterable[Mention]) -> int:
        rows = [
            (
                content_id,
                m.company,
                m.ticker,
                m.sentiment,
                m.score,
                m.confidence,
                m.quote,
                m.rationale,
                utcnow_iso(),
            )
            for m in mentions
        ]
        self.conn.executemany(
            """INSERT INTO mentions
               (content_id, company, ticker, sentiment, score, confidence, quote, rationale, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def mentions_since(self, since_iso: str, min_confidence: float = 0.0) -> list[Mention]:
        rows = self.conn.execute(
            """SELECT m.*, c.url AS c_url, c.published_at AS c_published
               FROM mentions m JOIN content_items c ON c.id = m.content_id
               WHERE m.created_at >= ? AND m.confidence >= ?
               ORDER BY m.created_at""",
            (since_iso, min_confidence),
        )
        out: list[Mention] = []
        for r in rows:
            out.append(
                Mention(
                    company=r["company"],
                    sentiment=r["sentiment"],
                    score=r["score"],
                    confidence=r["confidence"],
                    quote=r["quote"],
                    rationale=r["rationale"],
                    ticker=r["ticker"],
                    content_id=r["content_id"],
                    url=r["c_url"] or "",
                    published_at=r["c_published"],
                )
            )
        return out

    def mention_counts_since(self, since_iso: str) -> dict[str, int]:
        """Per-company mention counts since a timestamp (persistence factor)."""
        rows = self.conn.execute(
            "SELECT company, COUNT(*) AS n FROM mentions WHERE created_at >= ? GROUP BY company",
            (since_iso,),
        )
        return {r["company"]: r["n"] for r in rows}

    # --- meta / state ---
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def _row_to_item(r: sqlite3.Row) -> StoredItem:
    return StoredItem(
        id=r["id"],
        source=r["source"],
        external_id=r["external_id"],
        url=r["url"],
        author=r["author"],
        published_at=r["published_at"],
        text=r["text"],
        fetched_at=r["fetched_at"],
        analyzed=bool(r["analyzed"]),
    )

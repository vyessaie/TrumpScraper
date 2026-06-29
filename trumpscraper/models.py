"""Domain models shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RawItem:
    """A single piece of content fetched from a source, before analysis."""

    external_id: str          # stable id within the source (for dedupe)
    text: str
    source: str               # source name, e.g. "truth_social"
    url: str = ""
    author: str = "Donald Trump"
    published_at: str | None = None  # ISO 8601; falls back to fetch time


@dataclass
class StoredItem:
    id: int
    source: str
    external_id: str
    url: str
    author: str
    published_at: str | None
    text: str
    fetched_at: str
    analyzed: bool


@dataclass
class Mention:
    """A company/brand mention extracted from one content item."""

    company: str
    sentiment: str            # positive | negative | neutral | mixed
    score: float              # -1.0 (very negative) .. +1.0 (very positive)
    confidence: float         # 0.0 .. 1.0
    quote: str = ""
    rationale: str = ""
    ticker: str | None = None
    is_publicly_traded: bool = False
    # populated when read back from storage:
    content_id: int | None = None
    url: str = ""
    published_at: str | None = None

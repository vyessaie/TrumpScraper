"""Local file source.

Drop content into the inbox directory and it gets ingested on the next run:

- ``*.txt`` / ``*.md``: one statement per file (the whole file is one item).
- ``*.json``: either a single object or a list of objects with at least a
  ``text`` field; optional ``id``, ``url``, ``published_at``, ``author``.

This doubles as the manual path for audio you've transcribed elsewhere, or for
pasting in posts when live scraping is blocked. Files are read, not deleted, and
dedupe is handled by the store (external_id derives from the filename + index).
"""

from __future__ import annotations

import json
import logging
import os

from ..models import RawItem

log = logging.getLogger(__name__)

_TEXT_EXTS = {".txt", ".md"}


class LocalSource:
    name = "local"

    def __init__(self, inbox_dir: str = "inbox"):
        self.inbox_dir = inbox_dir

    def fetch(self) -> list[RawItem]:
        if not os.path.isdir(self.inbox_dir):
            return []
        items: list[RawItem] = []
        for fname in sorted(os.listdir(self.inbox_dir)):
            path = os.path.join(self.inbox_dir, fname)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(fname)[1].lower()
            try:
                if ext in _TEXT_EXTS:
                    items.extend(self._read_text(path, fname))
                elif ext == ".json":
                    items.extend(self._read_json(path, fname))
            except Exception as exc:
                log.warning("local: failed to read %s (%s)", fname, exc)
        log.info("local: ingested %d items from %s", len(items), self.inbox_dir)
        return items

    def _read_text(self, path: str, fname: str) -> list[RawItem]:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        if not text:
            return []
        return [RawItem(external_id=fname, text=text, source=self.name, url=f"file://{fname}")]

    def _read_json(self, path: str, fname: str) -> list[RawItem]:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        records = data if isinstance(data, list) else [data]
        out: list[RawItem] = []
        for i, rec in enumerate(records):
            text = (rec.get("text") or "").strip()
            if not text:
                continue
            ext_id = str(rec.get("id") or f"{fname}#{i}")
            out.append(
                RawItem(
                    external_id=ext_id,
                    text=text,
                    source=self.name,
                    url=rec.get("url", f"file://{fname}"),
                    author=rec.get("author", "Donald Trump"),
                    published_at=rec.get("published_at"),
                )
            )
        return out

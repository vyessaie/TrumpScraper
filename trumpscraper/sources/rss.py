"""Generic RSS/Atom feed source.

Powers archive/mirror feeds that are reliable from datacenter IPs, e.g.:

- ``trumpstruth.org`` — an independent archive mirroring every Truth Social
  post, with a public RSS feed (replaces direct Truth Social access, which
  blocks cloud IPs).
- ``whitehouse.gov`` remarks feed — official transcripts of speeches, press
  events, and remarks. With ``full_text: true`` the linked page is fetched and
  the article body extracted, since feed descriptions are often just excerpts.

Uses only the stdlib XML parser + ``requests``; no extra dependencies.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..models import RawItem
from .truth_social import strip_html

log = logging.getLogger(__name__)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)

# Hard ceiling per item so a malformed page can't blow up an API call.
DEFAULT_MAX_CHARS = 200_000


def parse_feed(xml_text: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into a list of entry dicts.

    Each dict has: title, link, guid, published (ISO or None), description.
    """
    root = ET.fromstring(xml_text)
    entries: list[dict] = []

    if root.tag.endswith("rss") or root.find("channel") is not None:
        for item in root.findall("./channel/item"):
            content = item.findtext(f"{_CONTENT_NS}encoded") or ""
            entries.append(
                {
                    "title": (item.findtext("title") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "guid": (item.findtext("guid") or "").strip(),
                    "published": _parse_date(item.findtext("pubDate")),
                    "description": content or (item.findtext("description") or ""),
                }
            )
    else:  # Atom
        for entry in root.findall(f"{_ATOM_NS}entry"):
            link = ""
            for ln in entry.findall(f"{_ATOM_NS}link"):
                if ln.get("rel") in (None, "alternate"):
                    link = ln.get("href", "")
                    break
            entries.append(
                {
                    "title": (entry.findtext(f"{_ATOM_NS}title") or "").strip(),
                    "link": link.strip(),
                    "guid": (entry.findtext(f"{_ATOM_NS}id") or "").strip(),
                    "published": _parse_date(
                        entry.findtext(f"{_ATOM_NS}published")
                        or entry.findtext(f"{_ATOM_NS}updated")
                    ),
                    "description": entry.findtext(f"{_ATOM_NS}content")
                    or entry.findtext(f"{_ATOM_NS}summary")
                    or "",
                }
            )
    return entries


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    try:  # RFC 822 ("Fri, 12 Jun 2026 13:00:00 +0000")
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:  # ISO 8601
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def extract_article_text(html_page: str) -> str:
    """Best-effort extraction of the main article text from an HTML page."""
    page = _SCRIPT_STYLE_RE.sub("", html_page)
    # Prefer the <article> element (WordPress wraps post content in one).
    start = page.find("<article")
    if start != -1:
        end = page.find("</article>", start)
        if end != -1:
            page = page[start:end]
    else:
        body = page.find("<body")
        if body != -1:
            page = page[body:]
    return strip_html(page)


class RssFeedSource:
    """One configured feed. ``source`` namespace in the DB is ``rss:<name>``."""

    def __init__(
        self,
        feed_name: str,
        url: str,
        *,
        full_text: bool = False,
        title_filter: list[str] | None = None,
        limit: int = 40,
        max_chars: int = DEFAULT_MAX_CHARS,
    ):
        self.name = f"rss:{feed_name}"
        self.url = url
        self.full_text = full_text
        self.title_filter = [t.lower() for t in (title_filter or [])]
        self.limit = limit
        self.max_chars = max_chars

    def fetch(self) -> list[RawItem]:
        try:
            import requests
        except ImportError:
            log.error("%s: 'requests' not installed", self.name)
            return []

        try:
            resp = requests.get(
                self.url, headers={"User-Agent": _USER_AGENT}, timeout=30
            )
            resp.raise_for_status()
            entries = parse_feed(resp.text)
        except Exception as exc:
            log.warning("%s: feed fetch failed (%s)", self.name, exc)
            return []

        items: list[RawItem] = []
        for entry in entries[: self.limit]:
            title = entry["title"]
            if self.title_filter and not any(
                t in title.lower() for t in self.title_filter
            ):
                continue
            text = self._entry_text(entry)
            if not text:
                continue
            if len(text) > self.max_chars:
                log.warning(
                    "%s: item '%s' truncated from %d to %d chars",
                    self.name, title[:60], len(text), self.max_chars,
                )
                text = text[: self.max_chars]
            ext_id = (
                entry["guid"]
                or entry["link"]
                or hashlib.sha256((title + (entry["published"] or "")).encode()).hexdigest()[:16]
            )
            items.append(
                RawItem(
                    external_id=ext_id,
                    text=text,
                    source=self.name,
                    url=entry["link"],
                    published_at=entry["published"],
                )
            )
        log.info("%s: fetched %d item(s)", self.name, len(items))
        return items

    def _entry_text(self, entry: dict) -> str:
        if self.full_text and entry["link"]:
            try:
                import requests

                resp = requests.get(
                    entry["link"], headers={"User-Agent": _USER_AGENT}, timeout=30
                )
                resp.raise_for_status()
                body = extract_article_text(resp.text)
                if body:
                    title = entry["title"]
                    return f"{title}\n\n{body}" if title else body
            except Exception as exc:
                log.warning(
                    "%s: full-text fetch failed for %s (%s); using feed summary",
                    self.name, entry["link"], exc,
                )
        return strip_html(entry["description"] or "")

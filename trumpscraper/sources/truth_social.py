"""Truth Social source.

Truth Social is a Mastodon fork, so an account's public posts are available at
``/api/v1/accounts/{id}/statuses``. This is the highest-volume source of Trump's
statements. Note: Truth Social sits behind anti-bot protection that can block
datacenter IPs; if requests fail, the source logs and returns an empty list, and
you can fall back to the ``local`` source (drop exported posts into ``inbox/``).
"""

from __future__ import annotations

import html
import logging
import re

from ..models import RawItem

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)


def strip_html(content: str) -> str:
    """Convert Mastodon HTML content to plain text."""
    # Preserve paragraph/line breaks before stripping tags.
    text = re.sub(r"</p>\s*<p>", "\n\n", content)
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()


class TruthSocialSource:
    name = "truth_social"

    def __init__(self, account_id: str, account: str, base_url: str, limit: int = 40):
        self.account_id = account_id
        self.account = account
        self.base_url = base_url.rstrip("/")
        self.limit = limit

    def fetch(self) -> list[RawItem]:
        if not self.account_id:
            log.warning("truth_social: no account_id configured; skipping")
            return []
        try:
            import requests
        except ImportError:
            log.error("truth_social: 'requests' not installed")
            return []

        url = f"{self.base_url}/api/v1/accounts/{self.account_id}/statuses"
        params = {"limit": self.limit, "exclude_replies": "true"}
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            statuses = resp.json()
        except Exception as exc:  # network, anti-bot block, JSON error, etc.
            log.warning("truth_social: fetch failed (%s). Falling back to other sources.", exc)
            return []

        items: list[RawItem] = []
        for status in statuses:
            text = strip_html(status.get("content", "") or "")
            # Skip pure re-posts / empty media-only posts with no text.
            if not text:
                continue
            items.append(
                RawItem(
                    external_id=str(status.get("id")),
                    text=text,
                    source=self.name,
                    url=status.get("url") or status.get("uri") or "",
                    author=self.account,
                    published_at=status.get("created_at"),
                )
            )
        log.info("truth_social: fetched %d posts", len(items))
        return items

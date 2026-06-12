"""Content sources. Each source yields :class:`RawItem` objects.

Sources are constructed from config by :func:`build_sources`. Heavy optional
dependencies (yt-dlp, whisper) are imported lazily inside the source that needs
them so the core pipeline runs without them installed.
"""

from __future__ import annotations

from typing import Any

from .base import Source
from .local import LocalSource
from .rss import RssFeedSource
from .truth_social import TruthSocialSource


def build_sources(sources_cfg: dict[str, Any]) -> list[Source]:
    sources: list[Source] = []

    rss = sources_cfg.get("rss", {})
    if rss.get("enabled"):
        for feed in rss.get("feeds", []):
            if not feed.get("url"):
                continue
            sources.append(
                RssFeedSource(
                    feed_name=feed.get("name", "feed"),
                    url=feed["url"],
                    full_text=bool(feed.get("full_text", False)),
                    title_filter=feed.get("title_filter"),
                    limit=int(feed.get("limit", 40)),
                )
            )

    ts = sources_cfg.get("truth_social", {})
    if ts.get("enabled"):
        sources.append(
            TruthSocialSource(
                account_id=ts.get("account_id", ""),
                account=ts.get("account", "realDonaldTrump"),
                base_url=ts.get("base_url", "https://truthsocial.com"),
                limit=int(ts.get("limit", 40)),
            )
        )

    local = sources_cfg.get("local", {})
    if local.get("enabled"):
        sources.append(LocalSource(inbox_dir=local.get("inbox_dir", "inbox")))

    audio = sources_cfg.get("audio", {})
    if audio.get("enabled") and audio.get("urls"):
        # Imported lazily — pulls in yt-dlp + whisper only when actually enabled.
        from .audio import AudioSource

        sources.append(
            AudioSource(
                urls=list(audio.get("urls", [])),
                whisper_model=audio.get("whisper_model", "base"),
            )
        )

    return sources


__all__ = ["Source", "TruthSocialSource", "LocalSource", "RssFeedSource", "build_sources"]

"""Configuration loading.

Settings come from a YAML file (defaults to ``config.yaml`` in the working
directory, or ``$TRUMPSCRAPER_CONFIG``) merged with environment variables.
Secrets (API keys, Telegram credentials) are read from the environment only and
are never written to the config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None


DEFAULT_CONFIG: dict[str, Any] = {
    "anthropic": {
        # Default to the most capable model; override per the claude-api skill.
        "model": "claude-opus-4-8",
        "max_tokens": 8000,
    },
    "sources": {
        # Archive/mirror feeds — primary sources; reliable from datacenter IPs.
        "rss": {
            "enabled": True,
            "feeds": [
                {
                    # Independent archive mirroring every Truth Social post.
                    "name": "trumps_truth",
                    "url": "https://trumpstruth.org/feed",
                    "limit": 40,
                },
                {
                    # Official transcripts of speeches / remarks / press events.
                    "name": "white_house",
                    "url": "https://www.whitehouse.gov/remarks/feed/",
                    "full_text": True,
                    "title_filter": ["president trump"],
                    "limit": 10,
                },
            ],
        },
        # Direct Truth Social API — blocks most cloud IPs; disabled by default
        # in favor of the trumps_truth mirror (also avoids duplicate posts).
        "truth_social": {
            "enabled": False,
            "account": "realDonaldTrump",
            # Trump's numeric Truth Social account id (Mastodon-style API).
            "account_id": "107780257626128497",
            "base_url": "https://truthsocial.com",
            "limit": 40,
        },
        "audio": {
            "enabled": False,
            "urls": [],          # YouTube / Rumble / direct media URLs of speeches
            "whisper_model": "base",
        },
        "local": {
            "enabled": True,
            "inbox_dir": "inbox",  # drop .txt / .json transcripts here
        },
    },
    "report": {
        "title": "Trump Market Mentions — Daily Digest",
        "lookback_hours": 24,
        "min_confidence": 0.5,
        "reports_dir": "reports",
    },
    "telegram": {
        "enabled": True,
        "parse_mode": "HTML",
    },
    "storage": {
        "db_path": "data/trumpscraper.db",
    },
}


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: _deep_copy(DEFAULT_CONFIG))

    # --- secrets (env only) ---
    anthropic_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        merged = _deep_copy(DEFAULT_CONFIG)
        path = path or os.environ.get("TRUMPSCRAPER_CONFIG", "config.yaml")
        if path and os.path.exists(path):
            if yaml is None:
                raise RuntimeError("PyYAML is required to read a config file")
            with open(path, "r", encoding="utf-8") as fh:
                user_cfg = yaml.safe_load(fh) or {}
            _deep_merge(merged, user_cfg)
        return cls(
            data=merged,
            anthropic_api_key=_clean_secret(os.environ.get("ANTHROPIC_API_KEY")),
            telegram_bot_token=_clean_secret(os.environ.get("TELEGRAM_BOT_TOKEN")),
            telegram_chat_id=_clean_secret(os.environ.get("TELEGRAM_CHAT_ID")),
        )

    # convenience accessors
    @property
    def anthropic(self) -> dict[str, Any]:
        return self.data["anthropic"]

    @property
    def sources(self) -> dict[str, Any]:
        return self.data["sources"]

    @property
    def report(self) -> dict[str, Any]:
        return self.data["report"]

    @property
    def telegram(self) -> dict[str, Any]:
        return self.data["telegram"]

    @property
    def db_path(self) -> str:
        return self.data["storage"]["db_path"]

    def with_overrides(self, **kwargs: Any) -> "Config":
        return replace(self, **kwargs)


def _clean_secret(value: str | None) -> str | None:
    """Normalize a pasted secret: trim whitespace/newlines and stray quotes.

    A trailing newline in a CI secret corrupts the HTTP header it's sent in,
    surfacing as a generic "Connection error" — strip defensively.
    """
    if value is None:
        return None
    cleaned = value.strip().strip("'\"").strip()
    return cleaned or None


def _deep_copy(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[k] = _deep_copy(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v)
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base

"""Deliver the report to Telegram via the Bot API.

Set up: create a bot with @BotFather to get TELEGRAM_BOT_TOKEN, then message the
bot (or add it to a channel) and obtain your chat id (e.g. via @userinfobot or
the getUpdates endpoint). Telegram caps messages at 4096 chars, so long reports
are split across multiple messages.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_LEN = 4096


def split_message(text: str, max_len: int = _MAX_LEN) -> list[str]:
    """Split text into chunks <= max_len, preferring line boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # A single line longer than the limit must be hard-split.
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_len])
            line = line[max_len:]
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "HTML",
    disable_preview: bool = True,
) -> int:
    """Send (possibly chunked) text. Returns the number of messages sent."""
    import requests

    sent = 0
    for chunk in split_message(text):
        resp = requests.post(
            _API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_preview,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Telegram sendMessage failed ({resp.status_code}): {resp.text}"
            )
        sent += 1
    log.info("telegram: sent %d message(s)", sent)
    return sent

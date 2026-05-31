"""TrumpScraper — monitor Trump's public statements for company mentions + sentiment.

Pipeline: gather content (Truth Social posts, transcribed speeches/streams, or
local files) -> analyze each item with Claude (company detection + sentiment) ->
store in SQLite -> compile a daily digest -> deliver via Telegram.
"""

__version__ = "0.1.0"

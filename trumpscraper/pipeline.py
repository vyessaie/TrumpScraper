"""Orchestrate the end-to-end pipeline: fetch -> analyze -> report -> deliver."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from .analyze import Analyzer, to_mentions
from .config import Config
from .report import Report, build_report, render_markdown, render_telegram_html
from .sources import build_sources
from .storage import Store
from . import telegram

log = logging.getLogger(__name__)


def fetch(config: Config, store: Store) -> int:
    """Fetch from all enabled sources; store new (deduped) items. Returns count."""
    sources = build_sources(config.sources)
    new_total = 0
    for source in sources:
        try:
            items = source.fetch()
        except Exception as exc:
            log.warning("source %s raised (%s); skipping", source.name, exc)
            continue
        new_ids = store.add_items(items)
        log.info("source %s: %d new of %d fetched", source.name, len(new_ids), len(items))
        new_total += len(new_ids)
    return new_total


def analyze(config: Config, store: Store, limit: int | None = None) -> int:
    """Analyze unprocessed items. Returns number of items analyzed."""
    pending = store.get_unanalyzed(limit=limit)
    if not pending:
        log.info("analyze: nothing to do")
        return 0

    analyzer = Analyzer(
        api_key=config.anthropic_api_key,
        model=config.anthropic["model"],
        max_tokens=int(config.anthropic.get("max_tokens", 8000)),
    )
    analyzed = 0
    for item in pending:
        try:
            result = analyzer.analyze(item.text, context=f"source: {item.source}")
        except Exception as exc:
            log.error("analyze: item %s failed (%s: %s)", item.id, type(exc).__name__, exc)
            continue
        mentions = to_mentions(result)
        store.add_mentions(item.id, mentions)
        store.mark_analyzed(item.id)
        analyzed += 1
        log.info("analyze: item %s -> %d mention(s)", item.id, len(mentions))
    return analyzed


def reanalyze_recent(config: Config, store: Store, days: int = 3) -> int:
    """Re-score items fetched in the last ``days`` days, then analyze them."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    n = store.reset_for_reanalysis(since)
    log.info("reanalyze: queued %d item(s) fetched since %s", n, since)
    analyze(config, store)
    return n


def build(config: Config, store: Store, total_items: int = 0) -> Report:
    """Build the report from mentions within the configured lookback window."""
    window_hours = int(config.report.get("lookback_hours", 24))
    min_conf = float(config.report.get("min_confidence", 0.5))
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    mentions = store.mentions_since(since, min_confidence=min_conf)
    if config.report.get("publicly_traded_only"):
        kept = [m for m in mentions if m.is_publicly_traded]
        dropped = sorted({m.company for m in mentions if not m.is_publicly_traded})
        log.info(
            "report: publicly_traded_only -> %d of %d mentions kept; "
            "dropped non-public: %s",
            len(kept), len(mentions), ", ".join(dropped) or "(none)",
        )
        mentions = kept
    report = build_report(
        mentions,
        title=config.report.get("title", "Trump Market Mentions"),
        window_hours=window_hours,
        total_items=total_items,
    )
    _attach_signals(config, store, report)
    return report


def _attach_signals(config: Config, store: Store, report: Report) -> None:
    """Generate buy/sell/hold screening signals for the report's companies."""
    if not (config.signals.get("enabled") and report.companies):
        return
    try:
        from .signals import SignalGenerator

        history_days = int(config.signals.get("history_days", 30))
        since = (datetime.now(timezone.utc) - timedelta(days=history_days)).isoformat()
        history_counts = store.mention_counts_since(since)
        generator = SignalGenerator(
            api_key=config.anthropic_api_key,
            model=config.anthropic["model"],
            max_tokens=int(config.anthropic.get("max_tokens", 8000)),
        )
        signals = generator.generate(report.companies, history_counts)
    except Exception as exc:
        log.warning("signals: skipped (%s: %s)", type(exc).__name__, exc)
        return
    for c in report.companies:
        c.signal = signals.get(c.company)
    log.info("signals: attached %d signal(s)", sum(1 for c in report.companies if c.signal))


def write_report_file(config: Config, report: Report) -> str:
    reports_dir = config.report.get("reports_dir", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(reports_dir, f"{date}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))
    log.info("report written to %s", path)
    return path


def is_actionable(report: Report) -> bool:
    """Whether a report is worth a notification.

    Actionable = at least one company carries a buy/sell-leaning signal. If
    signals couldn't be attached (disabled or generation failed) we fall back to
    "any company present" so a signal outage never silently drops real mentions.
    """
    if not report.companies:
        return False
    signaled = [c for c in report.companies if c.signal is not None]
    if signaled:
        return any(getattr(c.signal, "signal", None) in ("buy", "sell") for c in signaled)
    return True  # mentions exist but no signals attached — don't suppress


def deliver(config: Config, report: Report) -> bool:
    """Send the report to Telegram if configured. Returns True if sent."""
    if not config.telegram.get("enabled"):
        log.info("telegram delivery disabled")
        return False
    if not (config.telegram_bot_token and config.telegram_chat_id):
        log.warning("telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping")
        return False
    if config.telegram.get("only_when_actionable") and not is_actionable(report):
        log.info(
            "telegram: nothing actionable (%d companies, no buy/sell signal); "
            "skipping send. Full report still saved to the archive.",
            len(report.companies),
        )
        return False
    telegram.send_message(
        config.telegram_bot_token,
        config.telegram_chat_id,
        render_telegram_html(report),
        parse_mode=config.telegram.get("parse_mode", "HTML"),
    )
    return True


def run(config: Config) -> Report:
    """Full daily pipeline."""
    with Store(config.db_path) as store:
        new_items = fetch(config, store)
        analyzed = analyze(config, store)
        report = build(config, store, total_items=analyzed)
        write_report_file(config, report)
        deliver(config, report)
        store.set_meta("last_run", datetime.now(timezone.utc).isoformat())
        log.info(
            "run complete: %d new items, %d analyzed, %d companies in digest",
            new_items,
            analyzed,
            len(report.companies),
        )
        return report

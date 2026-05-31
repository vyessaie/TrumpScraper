"""Build the daily digest from accumulated mentions.

Mentions are grouped by company and aggregated (count, average sentiment score,
dominant sentiment label). Two renderers are provided: Markdown (for the
committed report file) and Telegram HTML (for the message).
"""

from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .models import Mention

_SENTIMENT_EMOJI = {
    "positive": "🟢",
    "negative": "🔴",
    "neutral": "⚪",
    "mixed": "🟡",
}


@dataclass
class CompanySummary:
    company: str
    ticker: str | None
    count: int
    avg_score: float
    label: str            # dominant sentiment
    mentions: list[Mention] = field(default_factory=list)


@dataclass
class Report:
    title: str
    generated_at: str
    window_hours: int
    total_items: int       # content items analyzed this run
    companies: list[CompanySummary]

    @property
    def total_mentions(self) -> int:
        return sum(c.count for c in self.companies)


def _label_from_score(score: float) -> str:
    if score >= 0.25:
        return "positive"
    if score <= -0.25:
        return "negative"
    return "neutral"


def build_report(
    mentions: list[Mention],
    *,
    title: str,
    window_hours: int,
    total_items: int,
) -> Report:
    grouped: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        grouped[m.company].append(m)

    companies: list[CompanySummary] = []
    for company, ms in grouped.items():
        avg = sum(m.score for m in ms) / len(ms)
        ticker = next((m.ticker for m in ms if m.ticker), None)
        companies.append(
            CompanySummary(
                company=company,
                ticker=ticker,
                count=len(ms),
                avg_score=avg,
                label=_label_from_score(avg),
                mentions=sorted(ms, key=lambda m: m.confidence, reverse=True),
            )
        )

    # Most-mentioned first, then strongest sentiment magnitude.
    companies.sort(key=lambda c: (c.count, abs(c.avg_score)), reverse=True)

    return Report(
        title=title,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        window_hours=window_hours,
        total_items=total_items,
        companies=companies,
    )


def render_markdown(report: Report) -> str:
    lines = [
        f"# {report.title}",
        "",
        f"_Generated {report.generated_at} · last {report.window_hours}h · "
        f"{report.total_items} statements analyzed · {report.total_mentions} company mentions_",
        "",
    ]
    if not report.companies:
        lines.append("No company mentions detected in this window.")
        return "\n".join(lines)

    for c in report.companies:
        emoji = _SENTIMENT_EMOJI.get(c.label, "⚪")
        ticker = f" (${c.ticker})" if c.ticker else ""
        lines.append(
            f"## {emoji} {c.company}{ticker} — {c.label} "
            f"(score {c.avg_score:+.2f}, {c.count} mention{'s' if c.count != 1 else ''})"
        )
        for m in c.mentions:
            quote = f"“{m.quote}”" if m.quote else "(no quote)"
            lines.append(f"- {quote}")
            if m.rationale:
                lines.append(f"  - {m.rationale}")
            if m.url and not m.url.startswith("file://"):
                lines.append(f"  - [source]({m.url})")
        lines.append("")
    return "\n".join(lines)


def render_telegram_html(report: Report) -> str:
    """Render an HTML message for Telegram's ``parse_mode=HTML``."""
    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    parts = [
        f"<b>{esc(report.title)}</b>",
        f"<i>last {report.window_hours}h · {report.total_items} statements · "
        f"{report.total_mentions} mentions</i>",
        "",
    ]
    if not report.companies:
        parts.append("No company mentions detected in this window.")
        return "\n".join(parts)

    for c in report.companies:
        emoji = _SENTIMENT_EMOJI.get(c.label, "⚪")
        ticker = f" (${esc(c.ticker)})" if c.ticker else ""
        parts.append(
            f"{emoji} <b>{esc(c.company)}</b>{ticker} — {c.label} "
            f"(score {c.avg_score:+.2f}, {c.count}×)"
        )
        # Show the single most confident quote to keep messages compact.
        top = c.mentions[0]
        if top.quote:
            parts.append(f"   “{esc(top.quote)}”")
        parts.append("")
    return "\n".join(parts)

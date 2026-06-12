"""Investment signal synthesis.

Takes the day's per-company mention summaries and asks Claude to score each
company on predefined factors, rolling them up into a directional signal:

- sentiment_strength: how strongly positive/negative the statements are
- materiality: rhetoric vs. concrete action (tariffs, contracts, probes, deals)
- specificity: company as the subject vs. a passing mention
- persistence: repeated mentions over the lookback window and recent history
- (channel weight is conveyed via the source of each mention: official
  White House remarks carry more weight than social posts)

Output per company: signal (buy/sell/hold) + conviction + horizon + rationale
+ key risks. These are screening signals derived from public statements only —
NOT financial advice — and the renderers label them as such.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SIGNAL_PROMPT = """You are an analyst converting Donald Trump's public statements about \
publicly traded companies into structured screening signals for an individual investor's \
daily digest.

You will receive, for each company: the stock ticker, every statement quote from the \
lookback window with its sentiment score (-1..+1), the source channel of each statement \
("rss:white_house" = official transcribed remarks, carry MORE weight; "rss:trumps_truth" \
= Truth Social post, carry less), and how often the company was mentioned recently.

For each company, score these predefined factors from 0.0 to 1.0:
1. sentiment_strength — magnitude and consistency of the sentiment expressed.
2. materiality — does the statement imply concrete action with business impact (tariffs, \
contracts, regulatory probes, government deals, procurement, sanctions), or is it pure \
rhetoric/opinion? Rhetoric scores low.
3. specificity — is the company the direct subject of the statement, or peripheral?
4. persistence — repeated mentions over time signal sustained attention; a single \
offhand mention scores low.

Then synthesize a signal:
- "buy": strongly positive sentiment AND material implications (favorable policy, \
contracts, endorsements with substance).
- "sell": strongly negative sentiment AND material threat (attacks coupled with \
regulatory/policy leverage, threatened tariffs, boycott calls).
- "hold": everything else — weak, mixed, immaterial, or rhetorical-only mentions \
default to hold. Be conservative: most single mentions are rhetorical and deserve "hold".

conviction: how strongly the factors line up ("low", "medium", "high"). A single \
rhetorical post can never exceed "low". horizon: the plausible duration of any market \
impact ("days", "weeks", "months") — statement-driven moves are usually short-lived, so \
prefer "days" unless there is concrete policy with a longer arc.

rationale: 1-2 plain-English sentences a non-finance person can follow.
risks: one sentence on the main reason this signal could be wrong.

Remember: these are screening signals from public statements only. Do not consider them \
sufficient basis for a trade. Be honest about weakness — "hold" with low conviction is a \
perfectly good answer."""


class FactorScores(BaseModel):
    sentiment_strength: float = Field(description="0.0-1.0")
    materiality: float = Field(description="0.0-1.0")
    specificity: float = Field(description="0.0-1.0")
    persistence: float = Field(description="0.0-1.0")


class CompanySignal(BaseModel):
    company: str
    ticker: str | None = None
    signal: Literal["buy", "sell", "hold"]
    conviction: Literal["low", "medium", "high"]
    horizon: Literal["days", "weeks", "months"]
    factors: FactorScores
    rationale: str
    risks: str


class SignalSet(BaseModel):
    signals: list[CompanySignal] = Field(default_factory=list)


def build_signal_input(companies, history_counts: dict[str, int]) -> str:
    """Serialize the report's company summaries for the signal call.

    ``companies`` is a list of report.CompanySummary.
    """
    blocks: list[str] = []
    for c in companies:
        lines = [
            f"COMPANY: {c.company} (ticker: {c.ticker or 'unknown'})",
            f"mentions in window: {c.count}; mentions in last 30 days: "
            f"{history_counts.get(c.company, c.count)}",
            f"average sentiment score: {c.avg_score:+.2f}",
            "statements:",
        ]
        for m in c.mentions:
            src = m.url or "unknown source"
            lines.append(
                f'  - [{m.sentiment} {m.score:+.2f}] "{m.quote or "(no quote)"}" '
                f"({src})"
            )
            if m.rationale:
                lines.append(f"    context: {m.rationale}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class SignalGenerator:
    def __init__(self, api_key: str | None, model: str, max_tokens: int = 8000):
        import anthropic  # lazy

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def generate(self, companies, history_counts: dict[str, int]) -> dict[str, CompanySignal]:
        """Return signals keyed by company name. Empty dict on failure."""
        if not companies:
            return {}
        user_content = build_signal_input(companies, history_counts)
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": SIGNAL_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
                output_format=SignalSet,
            )
        except Exception as exc:
            log.error("signals: generation failed (%s: %s)", type(exc).__name__, exc)
            return {}
        parsed = response.parsed_output
        if parsed is None:
            log.warning("signals: no parsed output")
            return {}
        return {s.company: s for s in parsed.signals}

"""Company-mention + sentiment extraction using the Claude API.

Each content item (a Truth Social post, a transcribed speech segment, etc.) is
sent to Claude with a structured-output schema. Claude auto-detects *any*
company/brand/publicly-traded entity Trump refers to and scores the sentiment he
expresses toward each.

The instruction prompt carries a ``cache_control`` breakpoint so it's reused
across the many per-item calls in a run. Note: prompt caching only engages once
the cached prefix exceeds the model's minimum (~4096 tokens on Opus 4.8); the
breakpoint is harmless below that and starts paying off if the prompt grows.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .models import Mention

SYSTEM_PROMPT = """You analyze public statements made by Donald Trump (Truth Social posts, \
speech transcripts, interview segments) to find references to companies and assess sentiment.

Your job, for the single statement provided:
1. Identify EVERY distinct company, brand, or publicly relevant business entity that is \
mentioned or clearly alluded to. Include private and public companies, well-known brands, \
and people when they stand in for a company (e.g. "Zuckerberg" -> Meta, "Bezos" -> Amazon). \
Resolve such references to the underlying company.
2. Do NOT report government agencies, countries, political figures, or generic industries \
unless they refer to a specific company. ("the auto industry" is NOT a company; "General \
Motors" is.)
3. For each company, determine the sentiment Trump expresses TOWARD that company in this \
statement — not the market's view, not your own. Use:
   - "positive": praise, endorsement, favorable framing
   - "negative": criticism, threats, attacks, unfavorable framing
   - "neutral": mentioned factually with no clear valence
   - "mixed": both positive and negative elements
4. score: a number from -1.0 (extremely negative) to +1.0 (extremely positive), 0.0 = neutral.
5. confidence: 0.0-1.0 — how confident you are this is a real company mention with that sentiment.
6. is_publicly_traded: true if the company (or its parent) has stock traded on a major \
exchange — judge this on what you know about the company, INDEPENDENTLY of whether you \
recall the exact ticker. Apple, Citigroup, Boeing, Meta, Amazon, Tesla, Truth Social's \
parent (Trump Media, DJT) are all true. Private companies, law firms, privately-held \
businesses, and government bodies are false.
7. ticker: the stock ticker symbol if you are confident of the exact symbol (e.g. "AAPL", \
"C", "MSFT"); otherwise null. It is fine to mark is_publicly_traded true with a null ticker \
when you know it is public but are unsure of the precise symbol. Do not guess a ticker.
8. quote: the short verbatim phrase from the statement that mentions the company.
9. rationale: one concise sentence explaining the sentiment call.

If no company is mentioned, return an empty mentions list. Be precise — false positives \
(flagging something that is not actually a company) are worse than misses. Provide a one-line \
overall summary of the statement."""


class CompanyMention(BaseModel):
    company: str = Field(description="Canonical company or brand name")
    is_publicly_traded: bool = Field(
        description="True if the company/parent trades on a major exchange, "
        "regardless of whether you know the exact ticker"
    )
    ticker: str | None = Field(default=None, description="Exact stock ticker if confident, else null")
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    score: float = Field(description="-1.0 (very negative) to 1.0 (very positive)")
    confidence: float = Field(description="0.0 to 1.0")
    quote: str = Field(default="", description="Verbatim phrase mentioning the company")
    rationale: str = Field(default="", description="One sentence explaining the sentiment")


class Analysis(BaseModel):
    summary: str = Field(description="One-line summary of the statement")
    mentions: list[CompanyMention] = Field(default_factory=list)


class Analyzer:
    """Wraps the Anthropic client. Lazily imported so the package loads without it."""

    def __init__(self, api_key: str | None, model: str, max_tokens: int = 8000):
        import anthropic  # lazy import

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def analyze(self, text: str, *, context: str = "") -> Analysis:
        """Analyze one statement and return structured mentions."""
        user_content = text if not context else f"[{context}]\n\n{text}"
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
            output_format=Analysis,
        )
        parsed = response.parsed_output
        if parsed is None:
            # Refusal or schema miss — treat as no mentions rather than crashing the run.
            return Analysis(summary="(analysis unavailable)", mentions=[])
        return parsed


def to_mentions(analysis: Analysis) -> list[Mention]:
    """Convert the LLM output schema into storage-layer Mention objects."""
    return [
        Mention(
            company=m.company.strip(),
            sentiment=m.sentiment,
            score=float(m.score),
            confidence=float(m.confidence),
            quote=m.quote.strip(),
            rationale=m.rationale.strip(),
            ticker=(m.ticker.strip().upper() if m.ticker else None),
            is_publicly_traded=bool(m.is_publicly_traded),
        )
        for m in analysis.mentions
        if m.company.strip()
    ]

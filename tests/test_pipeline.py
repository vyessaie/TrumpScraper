"""Unit tests that exercise the pipeline without network or API calls.

Run with: python -m unittest discover -s tests   (or: pytest)
"""

from __future__ import annotations

import os
import tempfile
import unittest

from trumpscraper.analyze import Analysis, CompanyMention, to_mentions
from trumpscraper.models import Mention, RawItem
from trumpscraper.report import build_report, render_markdown, render_telegram_html
from trumpscraper.sources.local import LocalSource
from trumpscraper.sources.truth_social import strip_html
from trumpscraper.storage import Store
from trumpscraper.telegram import split_message


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_dedupe(self):
        item = RawItem(external_id="x1", text="hello", source="local")
        self.assertIsNotNone(self.store.add_item(item))
        # Same (source, external_id) is ignored.
        self.assertIsNone(self.store.add_item(item))
        self.assertEqual(len(self.store.get_unanalyzed()), 1)

    def test_analyze_flow_and_lookback(self):
        rid = self.store.add_item(RawItem(external_id="x1", text="Apple is great", source="local"))
        self.store.add_mentions(
            rid,
            [Mention(company="Apple", sentiment="positive", score=0.8, confidence=0.9, ticker="AAPL")],
        )
        self.store.mark_analyzed(rid)
        self.assertEqual(len(self.store.get_unanalyzed()), 0)
        # Past window includes everything; min_confidence filters.
        recent = self.store.mentions_since("1970-01-01T00:00:00+00:00", min_confidence=0.5)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].company, "Apple")
        self.assertEqual(
            len(self.store.mentions_since("2999-01-01T00:00:00+00:00")), 0
        )


class SourceTests(unittest.TestCase):
    def test_strip_html(self):
        raw = "<p>Hello <b>world</b></p><p>Line two</p>"
        self.assertEqual(strip_html(raw), "Hello world\n\nLine two")

    def test_local_json_source(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "post.json"), "w", encoding="utf-8") as fh:
            fh.write('{"id": "a", "text": "Boeing is failing"}')
        with open(os.path.join(tmp, "note.txt"), "w", encoding="utf-8") as fh:
            fh.write("Tesla rocks")
        items = LocalSource(inbox_dir=tmp).fetch()
        texts = {i.text for i in items}
        self.assertEqual(texts, {"Boeing is failing", "Tesla rocks"})


class AnalyzeTests(unittest.TestCase):
    def test_to_mentions_filters_and_normalizes(self):
        analysis = Analysis(
            summary="x",
            mentions=[
                CompanyMention(
                    company=" Apple ", ticker="aapl", sentiment="positive",
                    score=0.8, confidence=0.9, quote="great job", rationale="praise",
                ),
                CompanyMention(
                    company="", ticker=None, sentiment="neutral",
                    score=0.0, confidence=0.5,
                ),
            ],
        )
        mentions = to_mentions(analysis)
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0].company, "Apple")
        self.assertEqual(mentions[0].ticker, "AAPL")


class ReportTests(unittest.TestCase):
    def _mentions(self):
        return [
            Mention(company="Apple", sentiment="positive", score=0.8, confidence=0.9,
                    quote="great job", ticker="AAPL"),
            Mention(company="Apple", sentiment="positive", score=0.6, confidence=0.8),
            Mention(company="Amazon", sentiment="negative", score=-0.7, confidence=0.85,
                    quote="ripping off", ticker="AMZN"),
        ]

    def test_build_groups_and_orders(self):
        report = build_report(self._mentions(), title="T", window_hours=24, total_items=2)
        self.assertEqual(report.total_mentions, 3)
        # Apple has 2 mentions -> first.
        self.assertEqual(report.companies[0].company, "Apple")
        self.assertEqual(report.companies[0].count, 2)
        self.assertAlmostEqual(report.companies[0].avg_score, 0.7)
        self.assertEqual(report.companies[0].label, "positive")
        self.assertEqual(report.companies[1].label, "negative")

    def test_renderers(self):
        report = build_report(self._mentions(), title="T", window_hours=24, total_items=2)
        md = render_markdown(report)
        self.assertIn("Apple", md)
        self.assertIn("$AAPL", md)
        html = render_telegram_html(report)
        self.assertIn("<b>Apple</b>", html)

    def test_empty_report(self):
        report = build_report([], title="T", window_hours=24, total_items=0)
        self.assertIn("No company mentions", render_markdown(report))


class TelegramTests(unittest.TestCase):
    def test_split_short(self):
        self.assertEqual(split_message("hello"), ["hello"])

    def test_split_long(self):
        text = "\n".join("line %d" % i for i in range(2000))
        chunks = split_message(text, max_len=200)
        self.assertTrue(all(len(c) <= 200 for c in chunks))
        self.assertEqual("\n".join(chunks).count("line"), 2000)

    def test_split_oversized_line(self):
        chunks = split_message("x" * 500, max_len=100)
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        self.assertEqual("".join(chunks), "x" * 500)


if __name__ == "__main__":
    unittest.main()

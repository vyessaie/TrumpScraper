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
from trumpscraper.sources.rss import RssFeedSource, extract_article_text, parse_feed
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


_RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Feed</title>
    <item>
      <title>Remarks by President Trump at an Event</title>
      <link>https://example.com/remarks-1</link>
      <guid>https://example.com/remarks-1</guid>
      <pubDate>Fri, 12 Jun 2026 13:00:00 +0000</pubDate>
      <description><![CDATA[<p>Apple is doing a <b>fantastic</b> job!</p>]]></description>
    </item>
    <item>
      <title>Press Briefing by the Press Secretary</title>
      <link>https://example.com/briefing-1</link>
      <guid>https://example.com/briefing-1</guid>
      <pubDate>Fri, 12 Jun 2026 14:00:00 +0000</pubDate>
      <description>Routine briefing.</description>
    </item>
  </channel>
</rss>"""


class RssTests(unittest.TestCase):
    def test_parse_feed_rss(self):
        entries = parse_feed(_RSS_FIXTURE)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["link"], "https://example.com/remarks-1")
        self.assertIn("2026-06-12", entries[0]["published"])
        self.assertIn("fantastic", entries[0]["description"])

    def test_parse_feed_atom(self):
        atom = (
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>T</title>"
            '<link rel="alternate" href="https://example.com/a"/>'
            "<id>tag:a</id><updated>2026-06-12T10:00:00Z</updated>"
            "<summary>Boeing is failing.</summary></entry></feed>"
        )
        entries = parse_feed(atom)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["link"], "https://example.com/a")
        self.assertEqual(entries[0]["description"], "Boeing is failing.")

    def test_title_filter_and_items(self):
        source = RssFeedSource(
            "white_house", "https://example.com/feed",
            title_filter=["president trump"],
        )
        entries = parse_feed(_RSS_FIXTURE)
        # Simulate the filtering/build step without network.
        kept = [
            e for e in entries
            if any(t in e["title"].lower() for t in source.title_filter)
        ]
        self.assertEqual(len(kept), 1)
        self.assertIn("President Trump", kept[0]["title"])

    def test_extract_article_text(self):
        page = (
            "<html><head><script>var x=1;</script></head><body>"
            "<nav>menu junk</nav>"
            "<article><h1>Remarks</h1><p>Tesla is great.</p></article>"
            "<footer>footer junk</footer></body></html>"
        )
        text = extract_article_text(page)
        self.assertIn("Tesla is great.", text)
        self.assertNotIn("menu junk", text)
        self.assertNotIn("var x=1", text)


class ConfigTests(unittest.TestCase):
    def test_clean_secret(self):
        from trumpscraper.config import _clean_secret
        self.assertEqual(_clean_secret("sk-ant-abc\n"), "sk-ant-abc")
        self.assertEqual(_clean_secret('  "sk-ant-abc"  '), "sk-ant-abc")
        self.assertEqual(_clean_secret("'token'"), "token")
        self.assertIsNone(_clean_secret("   "))
        self.assertIsNone(_clean_secret(None))


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

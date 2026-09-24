import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_news as fn  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
RFC822 = NOW.strftime("%a, %d %b %Y %H:%M:%S +0000")

RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>JPMorgan rolls out generative AI assistant to 200,000 bankers</title>
  <link>https://example.com/jpm-ai?utm_source=rss&amp;id=7</link>
  <description>&lt;p&gt;The bank expands its &lt;b&gt;LLM&lt;/b&gt; suite.&lt;/p&gt;</description>
  <pubDate>{RFC822}</pubDate></item>
<item><title>Football club wins the cup</title><link>https://example.com/sport</link>
  <pubDate>{RFC822}</pubDate></item>
<item><title>Bundesbank warnt vor Risiken bei Gewerbeimmobilien-Krediten</title>
  <link>https://example.com/buba</link><pubDate>{RFC822}</pubDate></item>
</channel></rss>"""

ATOM = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
<entry><title>EU-KI-Verordnung: BaFin legt Leitfaden für Banken vor</title>
  <link rel="alternate" href="https://example.org/ai-act"/>
  <updated>{NOW.isoformat().replace("+00:00", "Z")}</updated>
  <summary type="html">Die Aufsicht konkretisiert Anforderungen an KI-Modelle.</summary></entry>
</feed>"""

GNEWS = f"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>JPMorgan rolls out generative AI assistant to 200,000 bankers - Bloomberg</title>
  <link>https://news.google.com/rss/articles/abc</link>
  <description>&lt;a href="x"&gt;noise&lt;/a&gt;</description>
  <pubDate>{RFC822}</pubDate><source url="https://www.bloomberg.com">Bloomberg</source></item>
</channel></rss>"""


class ParsingTests(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = fn.parse_feed(RSS.encode())
        self.assertEqual(len(rss), 3)
        self.assertEqual(rss[0]["link"], "https://example.com/jpm-ai?utm_source=rss&id=7")
        atom = fn.parse_feed(ATOM.encode())
        self.assertEqual(atom[0]["link"], "https://example.org/ai-act")
        self.assertIsNotNone(fn.parse_date(atom[0]["date"]))

    def test_repairs_broken_xml(self):
        bad = b'<?xml version="1.0"?><rss><channel><item><title>AI & banks&nbsp;now</title>' \
              b'<link>https://x.com/?a=1&b=2</link></item></channel></rss>'
        entry = fn.parse_feed(bad)[0]
        self.assertEqual(entry["link"], "https://x.com/?a=1&b=2")
        self.assertIn("AI & banks", entry["title"])

    def test_loose_fallback_for_hopeless_xml(self):
        bad = b'<rss><channel><item><title><![CDATA[Banks <3 AI]]></title>' \
              b'<link>https://x.com/a</link><pubDate>Tue, 01 Sep 2026 10:00:00 GMT</pubDate>' \
              b'<description>a < b</description></item><item><title>Two</title>' \
              b'<link>https://x.com/b</link></item></channel></rss>'
        entries = fn.parse_feed(bad)
        self.assertEqual([e["link"] for e in entries], ["https://x.com/a", "https://x.com/b"])
        self.assertEqual(entries[0]["title"], "Banks <3 AI")

    def test_clean_text_and_url(self):
        self.assertEqual(fn.clean_text("&lt;p&gt;Die &lt;b&gt;Bank&lt;/b&gt;&lt;/p&gt;"), "Die Bank")
        self.assertEqual(fn.normalize_url("https://Ex.com/a?utm_source=x&id=1#top"), "https://ex.com/a?id=1")


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scorer = fn.Scorer(json.loads((ROOT / "config" / "topics.json").read_text()))

    def topics(self, title):
        return self.scorer.score(title, "", {})[0]

    def test_keywords(self):
        self.assertIn("ki", self.topics("Neues KI-Modell von Anthropic"))
        self.assertIn("finanzen", self.topics("Großbanken erhöhen Leitzins-Prognose"))
        self.assertIn("regulierung", self.topics("EU AI Act tritt in Kraft"))
        # Keine Fehltreffer: "said"/"maintain" enthalten "ai", "financial" enthaelt "fin".
        self.assertEqual(self.topics("He said the fed up fans remain calm"), [])

    def test_krypto_banken(self):
        topics, combos, _ = self.scorer.score("Deutsche Bank startet Stablecoin für Firmenkunden", "", {})
        self.assertIn("krypto", topics)
        self.assertIn("krypto-banken", combos)
        _, combos, _ = self.scorer.score("Sparkassen ermöglichen Bitcoin-Handel in der App", "", {})
        self.assertIn("krypto-banken", combos)
        self.assertNotIn("krypto", self.topics("Custody battle over the family circle"))

    def test_combo_ranks_higher(self):
        _, combos, both = self.scorer.score("Banks adopt generative AI", "", {"priority": 2})
        _, _, single = self.scorer.score("Football and AI", "", {"priority": 2})
        self.assertIn("ki-finanzen", combos)
        self.assertGreater(both, single)


class EndToEndTest(unittest.TestCase):
    def test_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name, body in (("a.xml", RSS), ("b.xml", ATOM), ("c.xml", GNEWS)):
                (tmp / name).write_text(body, encoding="utf-8")
            sources = {"sources": [
                {"name": "Direkt", "url": (tmp / "a.xml").as_uri(), "priority": 3},
                {"name": "Atom", "url": (tmp / "b.xml").as_uri(), "priority": 2},
                {"name": "GN", "url": (tmp / "c.xml").as_uri(), "type": "google_news"},
                {"name": "Kaputt", "url": (tmp / "fehlt.xml").as_uri()},
            ]}
            (tmp / "sources.json").write_text(json.dumps(sources))
            out = tmp / "out"
            cmd = [sys.executable, str(ROOT / "scripts" / "fetch_news.py"),
                   "--sources", str(tmp / "sources.json"), "--out", str(out), "--no-notify"]
            subprocess.run(cmd, check=True, capture_output=True)
            data = json.loads((out / "news.json").read_text())
            titles = [i["title"] for i in data["items"]]
            self.assertNotIn("Football club wins the cup", titles)
            # Direktmeldung und Google-News-Duplikat werden zusammengefasst.
            jpm = [i for i in data["items"] if i["title"].startswith("JPMorgan")]
            self.assertEqual(len(jpm), 1)
            self.assertEqual(jpm[0]["source"], "Direkt")
            self.assertEqual(jpm[0]["also"], ["Bloomberg"])
            self.assertTrue(jpm[0]["top"])
            self.assertEqual([s["ok"] for s in data["sources"]], [True, True, True, False])
            self.assertIn("Top-Meldungen", (out / "briefing.md").read_text())

            # Zweiter Lauf: keine doppelten Eintraege, first_seen bleibt erhalten.
            first_seen = jpm[0]["first_seen"]
            subprocess.run(cmd, check=True, capture_output=True)
            data2 = json.loads((out / "news.json").read_text())
            self.assertEqual(len(data2["items"]), len(data["items"]))
            self.assertEqual(
                next(i for i in data2["items"] if i["title"].startswith("JPMorgan"))["first_seen"], first_seen
            )

    def test_trusted_only_filters_unknown_publishers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.xml"
            path.write_text(GNEWS.replace("Bloomberg", "Vietnam.vn"), encoding="utf-8")
            source = {"name": "GN", "url": path.as_uri(), "type": "google_news", "trusted_only": True}
            scorer = fn.Scorer(json.loads((ROOT / "config" / "topics.json").read_text()))
            self.assertEqual(fn.collect_source(source, scorer, NOW, 50, ["Bloomberg"])[0], [])
            path.write_text(GNEWS, encoding="utf-8")
            self.assertEqual(len(fn.collect_source(source, scorer, NOW, 50, ["Bloomberg"])[0]), 1)

    def test_old_items_expire(self):
        old = (NOW - timedelta(days=30)).isoformat()
        items = fn.merge([{"id": "x", "title": "Alt", "published": old, "score": 9, "source": "s",
                           "notified": True, "first_seen": old}], [], NOW, {"keep_days": 10})
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Sammelt Meldungen aus RSS/Atom-Feeds, bewertet sie nach Themen und schreibt
docs/data/news.json (fuer die Web-App) und docs/data/briefing.md (Tagesueberblick).

Nur Python-Standardbibliothek, damit es ohne Installation in GitHub Actions laeuft.

    python scripts/fetch_news.py                 # normaler Lauf
    python scripts/fetch_news.py --no-notify     # ohne Push-Benachrichtigungen
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = (
    "Mozilla/5.0 (compatible; Nachrichten-Infozentrum/1.0; "
    "+https://github.com/SpookyOne1/Nachrichten)"
)
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_|cmpid$|ref$|src$|ncid$)", re.I)


# --------------------------------------------------------------------------- #
# Laden und Parsen der Feeds
# --------------------------------------------------------------------------- #
def download(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def clean_text(value: str | None, limit: int = 420) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)  # doppelt kodierte Entities
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " …"
    return text


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def repair_xml(data: bytes) -> bytes:
    """Behebt typische Fehler schlampig erzeugter Feeds: nackte '&', HTML-Entities, Steuerzeichen."""
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)(\w+;)?",
                  lambda m: html.escape(html.unescape(m.group(0))) if m.group(1) else "&amp;", text)
    text = re.sub(r"^<\?xml[^>]*\?>", "", text)
    return text.encode("utf-8")


def parse_feed(data: bytes) -> list[dict]:
    """Liest RSS 2.0, RSS 1.0 (RDF) und Atom. Gibt rohe Eintraege zurueck."""
    data = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        try:
            root = ET.fromstring(repair_xml(data))
        except ET.ParseError:
            return parse_feed_loose(data.decode("utf-8", errors="replace"))
    entries = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        entry: dict = {}
        for child in node:
            name = _local(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                entry["title"] = text
            elif name == "link":
                href = child.get("href")
                if href:  # Atom
                    if child.get("rel", "alternate") == "alternate" or "link" not in entry:
                        entry["link"] = href
                elif text:
                    entry["link"] = text
            elif name in ("description", "summary") and "summary" not in entry:
                entry["summary"] = text
            elif name in ("encoded", "content") and not entry.get("summary"):
                entry["summary"] = text
            elif name in ("pubdate", "published", "date", "issued"):
                entry["date"] = text
            elif name in ("updated", "modified") and "date" not in entry:
                entry["date"] = text
            elif name in ("guid", "id"):
                entry["guid"] = text
            elif name == "source":
                entry["source"] = text
        if entry.get("title") and (entry.get("link") or entry.get("guid", "").startswith("http")):
            entry.setdefault("link", entry.get("guid"))
            entries.append(entry)
    return entries


def parse_feed_loose(text: str) -> list[dict]:
    """Letzte Rueckfallstufe fuer kaputtes XML: Eintraege per Regex herauslesen."""
    def field(block: str, *names: str) -> str | None:
        for name in names:
            m = re.search(rf"<(?:\w+:)?{name}\b[^>]*>(.*?)</(?:\w+:)?{name}>", block, re.S | re.I)
            if m:
                value = m.group(1).strip()
                cdata = re.fullmatch(r"<!\[CDATA\[(.*?)\]\]>", value, re.S)
                return html.unescape(cdata.group(1) if cdata else value).strip()
        return None

    entries = []
    for block in re.findall(r"<(item|entry)\b[^>]*>(.*?)</\1>", text, re.S | re.I):
        body = block[1]
        link = field(body, "link")
        if not link:
            m = re.search(r"<link\b[^>]*href=[\"']([^\"']+)", body, re.I)
            link = html.unescape(m.group(1)) if m else field(body, "guid", "id")
        entry = {
            "title": field(body, "title"),
            "link": link,
            "summary": field(body, "description", "summary", "encoded", "content"),
            "date": field(body, "pubDate", "published", "date", "updated"),
        }
        if entry["title"] and entry["link"] and entry["link"].startswith("http"):
            entries.append({k: v for k, v in entry.items() if v})
    return entries


def normalize_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING_PARAMS.match(k)
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path, urllib.parse.urlencode(query), "")
    )


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", "", title.lower())[:90]


# --------------------------------------------------------------------------- #
# Themen-Erkennung und Bewertung
# --------------------------------------------------------------------------- #
def keyword_pattern(word: str) -> tuple[str, bool]:
    """Wandelt ein Stichwort in (Regex, case_sensitive) um."""
    lead = word.startswith("*")
    trail = word.endswith("*")
    core = word.strip("*")
    # Bindestrich zuerst ersetzen, sonst wuerde das "\-" aus der Leerzeichen-Regel erneut ersetzt.
    body = re.escape(core).replace(r"\-", r"[\s\-]?").replace(r"\ ", r"[\s\-]+")
    pattern = ("" if lead else r"(?<!\w)") + body + ("" if trail else r"(?!\w)")
    case_sensitive = len(core) <= 5 and any(c.isupper() for c in core)
    return pattern, case_sensitive


class Matcher:
    def __init__(self, words: list[str]):
        sensitive, insensitive = [], []
        for word in words:
            pattern, cs = keyword_pattern(word)
            (sensitive if cs else insensitive).append(pattern)
        self.cs = re.compile("|".join(sensitive)) if sensitive else None
        self.ci = re.compile("|".join(insensitive), re.I) if insensitive else None

    def find(self, text: str) -> list[str]:
        hits = []
        for rx in (self.cs, self.ci):
            if rx:
                hits += [m.group(0) for m in rx.finditer(text)]
        return hits


class Scorer:
    def __init__(self, cfg: dict):
        self.topics = cfg["topics"]
        self.matchers = {t["id"]: Matcher(t["keywords"]) for t in self.topics}
        self.weights = {t["id"]: t.get("weight", 1) for t in self.topics}
        self.combos = cfg.get("combos", [])
        boost = cfg.get("boost_words", {})
        self.boost = Matcher(boost.get("words", []))
        self.boost_points = boost.get("points", 0)

    def score(self, title: str, summary: str, source: dict) -> tuple[list[str], list[str], int]:
        topics, score = [], 0
        for tid, matcher in self.matchers.items():
            in_title = matcher.find(title)
            hits = in_title + matcher.find(summary)
            if not hits:
                continue
            topics.append(tid)
            # Titel-Treffer zaehlen voll, reine Teaser-Treffer halb; mehrere Treffer geben Bonus.
            base = self.weights[tid] if in_title else self.weights[tid] / 2
            score += base + min(len(set(h.lower() for h in hits)) - 1, 2)
        combos = [c["id"] for c in self.combos if all(t in topics for t in c["topics"])]
        score += sum(c.get("bonus", 0) for c in self.combos if c["id"] in combos)
        if topics and self.boost.find(title):
            score += self.boost_points
        if topics or source.get("include_all"):
            score += source.get("priority", 0)
        return topics, combos, round(score)


# --------------------------------------------------------------------------- #
# Hauptablauf
# --------------------------------------------------------------------------- #
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def collect_source(
    source: dict, scorer: Scorer, now: datetime, limit: int, trusted: list[str] = ()
) -> tuple[list[dict], dict]:
    status = {"name": source["name"], "ok": False, "count": 0, "error": None}
    try:
        entries = parse_feed(download(source["url"]))
    except Exception as exc:  # noqa: BLE001 - jede Stoerung einer Quelle nur melden
        status["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return [], status

    items = []
    for entry in entries[:limit]:
        title = clean_text(entry.get("title"), 300)
        display_source = source["name"]
        via = None
        summary = clean_text(entry.get("summary"))
        if source.get("type") == "google_news":
            origin = clean_text(entry.get("source")) or ""
            if origin and title.endswith(f" - {origin}"):
                title = title[: -len(origin) - 3].strip()
            display_source, via, summary = origin or "Google News", "Google News", ""
            if source.get("trusted_only") and not any(t.lower() in origin.lower() for t in trusted):
                continue
        if not title:
            continue
        topics, combos, score = scorer.score(title, summary, source)
        if not topics and not source.get("include_all"):
            continue
        published = parse_date(entry.get("date"))
        if published and published > now + timedelta(hours=1):
            published = now  # offensichtlich falsche Zukunftsdaten
        url = normalize_url(entry["link"])
        items.append(
            {
                "id": hashlib.sha1(url.encode()).hexdigest()[:14],
                "title": title,
                "url": url,
                "source": display_source,
                "feed": source["name"],
                "via": via,
                "lang": source.get("lang"),
                "published": published.isoformat() if published else None,
                "summary": summary if summary.lower() != title.lower() else "",
                "topics": topics,
                "combos": combos,
                "score": score,
            }
        )
    status.update(ok=True, count=len(items), fetched=len(entries))
    return items, status


def merge(previous: list[dict], fresh: list[dict], now: datetime, settings: dict) -> list[dict]:
    by_id: dict[str, dict] = {}
    for item in previous:
        by_id[item["id"]] = item
    for item in fresh:
        old = by_id.get(item["id"])
        if old:
            item["first_seen"] = old.get("first_seen")
            item["notified"] = old.get("notified", False)
            item["published"] = item["published"] or old.get("published")
        by_id[item["id"]] = item
    for item in by_id.values():
        item.setdefault("first_seen", now.isoformat())
        item.setdefault("notified", False)
        item["published"] = item.get("published") or item["first_seen"]

    # Gleiche Meldung aus mehreren Quellen (z. B. Direktfeed + Google News) zusammenfassen.
    by_title: dict[str, dict] = {}
    for item in sorted(by_id.values(), key=lambda i: (i.get("via") is not None, -i["score"])):
        key = title_key(item["title"])
        if key in by_title:
            keep = by_title[key]
            keep["also"] = sorted(set(keep.get("also", [])) | {item["source"]} - {keep["source"]})
            keep["notified"] = keep["notified"] or item["notified"]
            continue
        by_title[key] = item

    cutoff = now - timedelta(days=settings.get("keep_days", 10))
    items = [i for i in by_title.values() if parse_date(i["published"]) >= cutoff]
    items.sort(key=lambda i: i["published"], reverse=True)
    top = settings.get("top_score", 11)
    for item in items:
        item["top"] = item["score"] >= top
    return items[: settings.get("max_items", 1200)]


def send_notifications(items: list[dict], now: datetime, settings: dict, first_run: bool) -> int:
    threshold = settings.get("notify_score", 13)
    max_age = timedelta(hours=settings.get("notify_max_age_hours", 6))
    candidates = [
        i for i in items
        if not i["notified"] and i["score"] >= threshold and now - parse_date(i["published"]) <= max_age
    ]
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if first_run or not topic:
        # Beim ersten Lauf (oder ohne Konfiguration) nichts nachholen, nur als erledigt markieren.
        for item in candidates:
            item["notified"] = True
        return 0

    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    sent = 0
    for item in sorted(candidates, key=lambda i: -i["score"])[: settings.get("notify_max_per_run", 5)]:
        payload = {
            "topic": topic,
            "title": f"{item['source']}: {item['title']}"[:250],
            "message": item["summary"][:300] or "Neue wichtige Meldung",
            "click": item["url"],
            "priority": 4 if "ki-finanzen" in item["combos"] else 3,
            "tags": ["newspaper"],
        }
        headers = {"Content-Type": "application/json"}
        if os.environ.get("NTFY_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
        req = urllib.request.Request(server, data=json.dumps(payload).encode(), headers=headers)
        try:
            urllib.request.urlopen(req, timeout=15).close()
            sent += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ! Benachrichtigung fehlgeschlagen: {exc}", file=sys.stderr)
            continue
        item["notified"] = True
    # Alles, was ueber dem Limit lag, nicht spaeter nachschicken.
    for item in candidates:
        item["notified"] = True
    return sent


def write_briefing(items: list[dict], topics: list[dict], combos: list[dict], now: datetime) -> str:
    since = now - timedelta(hours=24)
    recent = [i for i in items if parse_date(i["published"]) >= since]
    recent.sort(key=lambda i: (-i["score"], i["published"]))
    stamp = now.astimezone(_berlin()).strftime("%d.%m.%Y, %H:%M")
    lines = [
        f"# Briefing KI · Innovation · Finanzen – {stamp} Uhr",
        "",
        f"{len(recent)} relevante Meldungen in den letzten 24 Stunden.",
        "",
    ]

    def block(title: str, subset: list[dict], n: int = 6):
        if not subset:
            return
        lines.extend([f"## {title}", ""])
        for i in subset[:n]:
            extra = f" – {i['summary']}" if i["summary"] else ""
            lines.append(f"- **[{i['title']}]({i['url']})** ({i['source']}){extra}")
        lines.append("")

    block("Top-Meldungen", [i for i in recent if i["top"]], 8)
    for combo in combos:
        block(combo["label"], [i for i in recent if combo["id"] in i["combos"]], 5)
    for topic in topics:
        block(topic["label"], [i for i in recent if topic["id"] in i["topics"]], 5)
    return "\n".join(lines)


def _berlin():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Berlin")
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "config" / "sources.json")
    parser.add_argument("--topics", type=Path, default=ROOT / "config" / "topics.json")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "data")
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()

    source_cfg = load_json(args.sources, {})
    sources = source_cfg["sources"]
    trusted = source_cfg.get("trusted_publishers", [])
    cfg = load_json(args.topics, {})
    settings = cfg.get("settings", {})
    scorer = Scorer(cfg)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    args.out.mkdir(parents=True, exist_ok=True)
    news_path = args.out / "news.json"
    previous = load_json(news_path, None)
    first_run = previous is None

    fresh, statuses = [], []
    limit = settings.get("max_items_per_feed", 60)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda s: collect_source(s, scorer, now, limit, trusted), sources)
        for source, (items, status) in zip(sources, results):
            fresh += items
            statuses.append(status)
            mark = "ok " if status["ok"] else "FEHLER"
            print(f"  [{mark}] {source['name']}: {status['count']} relevant" + (f" – {status['error']}" if status["error"] else ""))

    # Archiv bereinigen, falls eine Quelle inzwischen gefiltert oder entfernt wurde.
    filtered_feeds = {s["name"] for s in sources if s.get("trusted_only")}
    known_feeds = {s["name"] for s in sources}
    kept = [
        i for i in (previous or {}).get("items", [])
        if i.get("feed") in known_feeds
        and (i.get("feed") not in filtered_feeds or any(t.lower() in i["source"].lower() for t in trusted))
    ]
    items = merge(kept, fresh, now, settings)
    sent = 0 if args.no_notify else send_notifications(items, now, settings, first_run)

    data = {
        "generated_at": now.isoformat(),
        "topics": [{k: t[k] for k in ("id", "label", "color")} for t in cfg["topics"]],
        "combos": [{k: c[k] for k in ("id", "label", "topics")} for c in cfg.get("combos", [])],
        "sources": statuses,
        "items": items,
    }
    tmp = news_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(news_path)
    (args.out / "briefing.md").write_text(
        write_briefing(items, cfg["topics"], cfg.get("combos", []), now), encoding="utf-8"
    )

    ok = sum(s["ok"] for s in statuses)
    print(f"{len(items)} Meldungen gespeichert, {ok}/{len(statuses)} Quellen erreichbar, {sent} Push-Nachrichten.")
    # Nur fehlschlagen, wenn gar keine Quelle funktioniert (dann stimmt grundsaetzlich etwas nicht).
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

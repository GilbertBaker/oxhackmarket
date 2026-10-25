"""Cherwell / Oxford Student feed scraper (owner C, PLAN section 7).

RSS only (the papers run WordPress, so /feed/ works). Stores headline, short summary and
link, never the full article. Dedupes on URL (articles.url is UNIQUE). Polite: one request
per feed per pass, a real User-Agent, and the pass runs every content.scrape_minutes.

    python server/scraper.py          scrape once and print what was new
"""
import html
import logging
import re
from datetime import datetime, timezone

import requests

import db

log = logging.getLogger("scraper")

USER_AGENT = "OxMarket/1.0 (OxHack student project; headline + link only)"
FEEDS = [
    ("Cherwell", "https://cherwell.org/category/news/feed/"),
    ("Cherwell", "https://cherwell.org/feed/"),
    ("The Oxford Student", "https://www.oxfordstudent.com/category/news/feed/"),
    ("The Oxford Student", "https://www.oxfordstudent.com/feed/"),
]
SUMMARY_CHARS = 400
_TAG = re.compile(r"<[^>]+>")


def _clean(text):
    text = html.unescape(_TAG.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    # WordPress appends "The post X appeared first on Y."
    text = re.sub(r"\s*The post .* appeared first on .*$", "", text)
    if len(text) > SUMMARY_CHARS:
        text = text[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def _published(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def fetch_feed(source, url, timeout=20):
    """[{source, url, title, summary, published_at}] from one RSS feed ([] on failure)."""
    import feedparser   # lazy: only the scraper needs it
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("feed %s failed: %s", url, e)
        return []
    feed = feedparser.parse(resp.content)
    out = []
    for e in feed.entries:
        link, title = (e.get("link") or "").strip(), _clean(e.get("title"))
        if not link or not title:
            continue
        out.append({"source": source, "url": link.split("?")[0], "title": title,
                    "summary": _clean(e.get("summary") or e.get("description")),
                    "published_at": _published(e)})
    return out


def scrape_all(propose=True):
    """Fetch every feed, insert new articles. Then (propose=True) let content turn new
    articles into proposed markets (Gemini only; without a key they wait for an admin).
    Returns the number of new articles."""
    seen, new = set(), 0
    for source, url in FEEDS:
        items = fetch_feed(source, url)
        for a in items:
            if a["url"] in seen:
                continue
            seen.add(a["url"])
            if db.insert_article(a) is not None:
                new += 1
        log.info("feed %s: %d items", url, len(items))
    log.info("scrape done: %d new articles", new)
    if propose and new:
        import content
        content.process_new_articles()
    return new


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    n = scrape_all(propose="--no-propose" not in sys.argv)
    for a in db.recent_articles(limit=max(n, 10)):
        print(f"{a['id']:>5}  {a['source']:<20} {a['title'][:90]}")

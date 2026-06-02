"""Crawler Tier 3 — RSS Financial News Aggregator (SDAD §3.3).

Captures ad-hoc ESG risk signals from third-party financial press [Vu et al.
2023]. Pairs naturally with the deduplication module so the same story
syndicated across outlets is only ingested once.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# RSS sources from SDAD §3.3.
RSS_FEEDS: dict[str, str] = {
    "CafeF - Doanh nghiep": "https://cafef.vn/tin-kinh-te-vi-mo.rss",
    "Vietstock - Doanh nghiep": "https://vietstock.vn/rss/doanh-nghiep.rss",
}


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    published: str
    body_text: str

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "link": self.link,
            "published": self.published,
            "body_text": self.body_text,
        }


def fetch_feed(source_name: str, url: str) -> list[NewsItem]:
    """Parse a single RSS feed into NewsItem records (requires feedparser)."""
    try:
        import feedparser
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "feedparser is required for Tier 3 (pip install feedparser)"
        ) from exc

    parsed = feedparser.parse(url)
    items: list[NewsItem] = []
    for entry in parsed.entries:
        body = entry.get("summary", "") or entry.get("description", "")
        items.append(
            NewsItem(
                source=source_name,
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                published=entry.get("published", ""),
                body_text=body,
            )
        )
    return items


def aggregate_all(feeds: dict[str, str] | None = None) -> list[NewsItem]:
    """Fetch every configured feed, tolerating individual source failures."""
    feeds = feeds or RSS_FEEDS
    collected: list[NewsItem] = []
    for name, url in feeds.items():
        try:
            collected.extend(fetch_feed(name, url))
        except Exception as exc:  # noqa: BLE001 - keep other feeds alive
            logger.warning("RSS feed '%s' failed: %s", name, exc)
    return collected

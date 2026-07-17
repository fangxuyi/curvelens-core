"""
Product-profile-driven RSS news collector.

Fetches articles from curated free RSS feeds, filters by recency and
energy-relevance, and stores as a JSON array in the raw layer for
downstream LLM catalyst extraction via extract_catalysts.py.

Raw storage path: data/raw/rss_news/{retrieval_date}/rss_news_{YYYYMMDD}.json
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import feedparser
import httpx

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

# Lookback window: include articles published within this many days of as_of
_LOOKBACK_DAYS = 7

_USER_AGENT = "CurveLensBot/1.0 (https://github.com/fangxuyi/curveLens)"

class RSSNewsCollector:
    """Fetches energy news from free RSS feeds for a given trade date.

    Stores one JSON file per trade_date containing all filtered articles.
    Follows the same collect() return-dict contract as other CurveLens collectors.
    """

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        product = get_product()
        self.source_id = f"rss_news_{product.key}"
        self.keywords = frozenset(product.news.keywords)
        self.sources = [
            {"key": key, "url": url, "name": name}
            for key, url, name in product.news.sources
        ]

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        cutoff = as_of_date - timedelta(days=_LOOKBACK_DAYS)
        articles: list[dict] = []
        seen_urls: set[str] = set()
        source_results: dict[str, str] = {}

        if not self.sources or not self.keywords:
            self.manifest_db.complete_run(
                run_id, "success", 0, 0, 0, 1,
                notes="product has no RSS sources or relevance keywords",
            )
            return {"run_id": run_id, "status": "skipped", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1,
                    "articles": 0, "sources": {}}

        for source in self.sources:
            try:
                fetched = _fetch_source(source, cutoff, seen_urls, self.keywords)
                articles.extend(fetched)
                source_results[source["key"]] = f"ok ({len(fetched)})"
                logger.info("RSS %s: %d articles", source["key"], len(fetched))
            except Exception as exc:
                source_results[source["key"]] = f"failed: {exc}"
                logger.warning("RSS %s: failed — %s", source["key"], exc)

        if not articles:
            logger.warning("No RSS articles fetched for %s", as_of_str)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0)
            return {
                "run_id": run_id, "status": "warning",
                "success": 0, "warning": 1, "failure": 0, "skipped": 0,
                "articles": 0, "sources": source_results,
            }

        content = json.dumps(articles, indent=2, ensure_ascii=False).encode("utf-8")
        filename = f"{self.source_id}_{as_of_date.strftime('%Y%m%d')}.json"
        sha256 = hashlib.sha256(content).hexdigest()

        if self.manifest_db.sha256_exists_for_date(sha256, as_of_str):
            logger.info("RSS news already stored for %s (unchanged)", as_of_str)
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {
                "run_id": run_id, "status": "success",
                "success": 0, "warning": 0, "failure": 0, "skipped": 1,
                "articles": len(articles), "sources": source_results,
            }

        raw_path, sha256, byte_size = self.raw_store.persist(
            content=content,
            source_id=self.source_id,
            filename=filename,
            trade_date=as_of_str,
            source_url="rss_feeds",
            http_status=200,
            content_type="application/json",
        )
        logger.info("RSS news stored: %d articles → %s", len(articles), raw_path)

        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": self.source_id,
            "raw_path": str(raw_path),
            "sha256": sha256,
            "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": as_of_str,
            "source_url": "rss_feeds",
            "http_status": 200,
            "content_type": "application/json",
            "collection_run_id": run_id,
        })

        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0)
        return {
            "run_id": run_id, "status": "success",
            "success": 1, "warning": 0, "failure": 0, "skipped": 0,
            "articles": len(articles), "sources": source_results,
        }


def find_raw_articles(data_dir: Path, as_of_date: date) -> Optional[Path]:
    """Find the raw RSS articles JSON file for a given trade date.

    Returns the path if found, None otherwise. Searches all retrieval-date
    subdirectories under data/raw/rss_news/ (most recent first).
    """
    product = get_product()
    source_ids = [f"rss_news_{product.key}", "rss_news"]
    for source_id in source_ids:
        filename = f"{source_id}_{as_of_date.strftime('%Y%m%d')}.json"
        rss_dir = data_dir / "raw" / source_id
        if not rss_dir.exists():
            continue
        for child in sorted(rss_dir.iterdir(), reverse=True):
            if child.is_dir():
                candidate = child / filename
                if candidate.exists():
                    return candidate
    return None


def _fetch_source(source: dict, cutoff: date, seen_urls: set,
                  keywords: frozenset[str]) -> list[dict]:
    """Fetch one RSS feed; return filtered, deduplicated article dicts."""
    with httpx.Client(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        resp = client.get(source["url"])
        resp.raise_for_status()
        feed_content = resp.text

    feed = feedparser.parse(feed_content)
    articles = []

    for entry in feed.entries:
        url = getattr(entry, "link", "") or ""
        if not url or url in seen_urls:
            continue

        title = getattr(entry, "title", "") or ""
        summary = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )
        text = f"{title}. {summary}".strip(". ")
        if not text:
            continue

        if not any(kw in text.lower() for kw in keywords):
            continue

        published_at = _parse_entry_date(entry) or date.today()
        if published_at < cutoff:
            continue

        seen_urls.add(url)
        articles.append({
            "title": title,
            "text": text,
            "url": url,
            "published_at": published_at.isoformat(),
            "source_key": source["key"],
            "source_name": source["name"],
        })

    return articles


def _parse_entry_date(entry) -> Optional[date]:
    """Extract publish date from a feedparser entry struct."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return date(val.tm_year, val.tm_mon, val.tm_mday)
            except (TypeError, ValueError, AttributeError):
                pass
    return None

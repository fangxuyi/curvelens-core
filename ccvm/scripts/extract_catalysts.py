#!/usr/bin/env python
"""
Catalyst extraction pipeline.

Reads article text from a JSON file, a single text file, or automatically
from raw RSS articles collected by collect_day.py. Extracts structured
catalyst events using Claude, ranks them against the current futures curve,
and saves to data/gold/events/.

Usage:
    # Auto-read from raw RSS store (after collect_day.py --source rss_news):
    python scripts/extract_catalysts.py --date 2026-06-25

    # Extract from an explicit JSON file of articles:
    python scripts/extract_catalysts.py --date 2026-06-25 --articles articles.json

    # Extract from a single text file:
    python scripts/extract_catalysts.py --date 2026-06-25 --text article.txt \
        --url https://example.com/article --published 2026-06-25

Articles JSON format:
    [{"text": "...", "url": "...", "published_at": "YYYY-MM-DD"}, ...]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from ccvm.agents.catalyst_extractor import extract
from ccvm.agents.catalyst_ranker import rank_events
from ccvm.agents.catalyst_store import CatalystStore
from ccvm.collectors.rss import find_raw_articles
from ccvm.storage.parquet_store import ParquetStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Observation date YYYY-MM-DD")
    parser.add_argument("--articles", help="Path to JSON array of articles")
    parser.add_argument("--text", help="Path to a single article text file")
    parser.add_argument("--url", default="", help="Source URL for single article")
    parser.add_argument("--published", help="Published date for single article")
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid date {args.date!r}")
        sys.exit(1)

    # ── Collect articles to process ──
    articles: list[dict] = []

    if args.articles:
        path = Path(args.articles)
        if not path.exists():
            print(f"ERROR: file not found: {path}")
            sys.exit(1)
        articles = json.loads(path.read_text())
        if not isinstance(articles, list):
            articles = [articles]

    elif args.text:
        path = Path(args.text)
        if not path.exists():
            print(f"ERROR: file not found: {path}")
            sys.exit(1)
        articles = [{
            "text": path.read_text(),
            "url": args.url,
            "published_at": args.published or args.date,
        }]

    else:
        # Auto-detect: look for raw RSS articles stored by collect_day.py
        raw_path = find_raw_articles(DATA_DIR, as_of)
        if raw_path:
            logger.info("Auto-loading RSS articles from %s", raw_path)
            articles = json.loads(raw_path.read_text())
            if not isinstance(articles, list):
                articles = [articles]
        else:
            print(
                f"No articles found. Run: python scripts/collect_day.py --date {args.date} --source rss_news\n"
                "Or provide --articles or --text explicitly."
            )
            sys.exit(1)

    # ── Get front-month contract from gold features ──
    pq = ParquetStore(DATA_DIR)
    front_delivery_month = None
    if pq.exists("gold", "futures_features", args.date):
        try:
            gold_fut = pq.read("gold", "futures_features", args.date)
            d = gold_fut.to_pydict()
            if d["delivery_month"]:
                front_delivery_month = d["delivery_month"][0]
        except Exception as exc:
            logger.warning("Could not read gold futures: %s", exc)

    # ── Extract ──
    store = CatalystStore(DATA_DIR)
    events: list[dict] = []

    for i, article in enumerate(articles):
        text = article.get("text", "")
        url = article.get("url", "")
        published = article.get("published_at", args.date)

        logger.info("Extracting from article %d/%d: %s", i + 1, len(articles), url[:80])
        event = extract(
            text=text,
            source_url=url,
            published_at=published,
            observation_date=as_of,
        )
        if event:
            events.append(event)
            logger.info("  → %s [%s] %s",
                        event.get("event_type"), event.get("direction"), event.get("title"))
        else:
            logger.info("  → no catalyst extracted")

    if not events:
        logger.warning("No catalyst events extracted from %d articles", len(articles))
        sys.exit(0)

    # ── Rank ──
    ranked = rank_events(events, as_of, front_delivery_month)

    # ── Save ──
    written = store.save(ranked, as_of)
    logger.info("Saved %d new catalyst events for %s (deduped: %d skipped)",
                written, args.date, len(ranked) - written)

    # ── Print summary ──
    print(f"\n{'─'*60}")
    print(f"Top catalysts for {args.date}:")
    for ev in ranked[:5]:
        print(f"  #{ev['relevance_rank']}  [{ev['relevance_score']:3d}]  "
              f"{ev['direction']:20s}  {ev['title'][:60]}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Run CurveLens data collection for a given date.

Usage:
    python scripts/collect_day.py --date 2026-06-24 --source yfinance_futures
    python scripts/collect_day.py --date 2026-06-24 --source cme_bulletin_pdf
    python scripts/collect_day.py --date 2026-06-24 --source eia
    python scripts/collect_day.py --date 2026-06-24 --source rss_news
    python scripts/collect_day.py --date 2026-06-24 --source all

Option data (cme_bulletin_pdf):
    Requires data/cme_bulletin/<YYYY-MM-DD>.pdf downloaded from the URL in the
    active product profile.

Agent-framework analysis consumes the stored articles from its evidence packets;
this collector makes no model calls.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Add src to path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env from the ccvm/ project root
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from ccvm.collectors.cme_bulletin_pdf import CMEBulletinPDFCollector
from ccvm.collectors.authorized_market_data import AuthorizedMarketDataCollector
from ccvm.collectors.csv_futures import CSVFuturesCollector
from ccvm.collectors.fred_macro import FREDMacroCollector
from ccvm.fundamentals import get_provider
from ccvm.reference.product import get_product
from ccvm.collectors.rss import RSSNewsCollector
from ccvm.collectors.cftc_cot import CFTCCOTCollector
from ccvm.collectors.yfinance_brent import YFinanceBenchmarkCollector
from ccvm.collectors.yfinance_futures import YFinanceFuturesCollector
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore
from ccvm.runtime import data_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = data_dir()
MANIFEST_DB_PATH = DATA_DIR / "manifests" / "manifest.duckdb"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "futures"

_SOURCES = ["yfinance_futures", "yfinance_benchmark", "yfinance_brent", "cftc_cot",
            "cme_bulletin_pdf", "fundamentals", "eia", "macro", "fred_macro",
            "authorized_market_data", "rss_news", "news", "market",
            "csv_futures", "all"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect CurveLens data for a given date")
    parser.add_argument("--date", required=True, help="Trade date (YYYY-MM-DD)")
    parser.add_argument(
        "--source",
        choices=_SOURCES,
        default="all",
        help=("Which collector(s) to run (default: all; unsupported optional "
              "capabilities are skipped from the active product profile)"),
    )
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid date format {args.date!r}; expected YYYY-MM-DD")
        sys.exit(1)

    raw_store = RawStore(DATA_DIR)
    manifest_db = ManifestDB(MANIFEST_DB_PATH)
    results = {}
    product = get_product()
    uses_authorized_files = (
        product.market_data is not None
        and product.market_data.provider == "authorized_files"
    )

    if args.source in ("authorized_market_data", "market", "all"):
        if not uses_authorized_files:
            if args.source == "authorized_market_data":
                print("[authorized_market_data] skipped — not configured")
        else:
            collector = AuthorizedMarketDataCollector(
                DATA_DIR, raw_store, manifest_db,
            )
            result = collector.collect(as_of)
            results["authorized_market_data"] = result
            print(f"[authorized_market_data] {result}")

    if args.source in ("yfinance_futures", "market", "all"):
        if uses_authorized_files:
            print(
                "[yfinance_futures]  skipped — authoritative files are required "
                "by the active product"
            )
        else:
            collector = YFinanceFuturesCollector(raw_store, manifest_db)
            result = collector.collect(as_of)
            results["yfinance_futures"] = result
            print(f"[yfinance_futures]  {result}")

    if args.source in ("yfinance_benchmark", "yfinance_brent", "market", "all"):
        collector = YFinanceBenchmarkCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["yfinance_benchmark"] = result
        print(f"[benchmark]          {result}")

    if args.source in ("cftc_cot", "market", "all"):
        collector = CFTCCOTCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["cftc_cot"] = result
        print(f"[cftc_cot]          {result}")

    if args.source in ("cme_bulletin_pdf", "market", "all"):
        if uses_authorized_files:
            print(
                "[cme_bulletin_pdf]  skipped — authoritative files are required "
                "by the active product"
            )
        elif get_product().bulletin is None:
            print("[cme_bulletin_pdf]  skipped — product has no bulletin configuration")
        else:
            collector = CMEBulletinPDFCollector(DATA_DIR, raw_store, manifest_db)
            result = collector.collect(as_of)
            results["cme_bulletin_pdf"] = result
            print(f"[cme_bulletin_pdf]  {result}")

    if args.source in ("fundamentals", "eia", "market", "all"):
        # E4: the fundamentals collector comes from the product profile registry
        provider = get_provider(get_product().fundamentals_provider)
        collector = provider.collector_cls(raw_store, manifest_db) if provider else None
    if args.source in ("fundamentals", "eia", "market", "all") and collector is None:
        print("[fundamentals]      skipped — product has no fundamentals_provider")
    elif args.source in ("fundamentals", "eia", "market", "all"):
        result = collector.collect(as_of)
        results["fundamentals"] = result
        print(f"[fundamentals]      {result}")

    if args.source in ("macro", "fred_macro", "market", "all"):
        macro = get_product().macro
        if macro is None:
            print("[macro]             skipped — product has no macro capability")
        elif macro.provider != "fred":
            print(f"[macro]             skipped — unsupported provider {macro.provider!r}")
        else:
            result = FREDMacroCollector(raw_store, manifest_db).collect(as_of)
            results["macro"] = result
            print(f"[macro]             {result}")

    if args.source in ("rss_news", "news", "all"):
        collector = RSSNewsCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["rss_news"] = result
        n = result.get("articles", 0)
        print(f"[rss_news]          {result['status']} — {n} articles  {result.get('sources', {})}")

    if args.source == "csv_futures":
        collector = CSVFuturesCollector(FIXTURES_DIR, raw_store, manifest_db)
        result = collector.collect(as_of)
        results["csv_futures"] = result
        print(f"[csv_futures]       {result}")

    any_failure = any(r.get("status") == "failed" for r in results.values())
    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()

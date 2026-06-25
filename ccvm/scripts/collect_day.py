#!/usr/bin/env python
"""Run CCVM data collection for a given date.

Usage:
    python scripts/collect_day.py --date 2026-06-24 --source yfinance_futures
    python scripts/collect_day.py --date 2026-06-24 --source yfinance_options
    python scripts/collect_day.py --date 2026-06-24 --source eia
    python scripts/collect_day.py --date 2026-06-24 --source all
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Add src to path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.collectors.barchart_options import BarchartOptionsCollector
from ccvm.collectors.cme_futures import CMEFuturesCollector
from ccvm.collectors.cme_options import CMEOptionsCollector
from ccvm.collectors.csv_futures import CSVFuturesCollector
from ccvm.collectors.eia import EIACollector
from ccvm.collectors.etrade_options import ETradeOptionsCollector
from ccvm.collectors.yfinance_futures import YFinanceFuturesCollector
from ccvm.collectors.yfinance_options import YFinanceOptionsCollector
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MANIFEST_DB_PATH = DATA_DIR / "manifests" / "manifest.duckdb"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "futures"

_SOURCES = [
    "yfinance_futures", "barchart_options", "etrade_options",
    "yfinance_options", "eia", "csv_futures",
    "cme_futures", "cme_options", "all",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect CCVM data for a given date")
    parser.add_argument("--date", required=True, help="Trade date (YYYY-MM-DD)")
    parser.add_argument(
        "--source",
        choices=_SOURCES,
        default="all",
        help="Which collector(s) to run (default: all = yfinance_futures + yfinance_options + eia)",
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

    # Primary web collectors (yfinance — free, no API key required)
    if args.source in ("yfinance_futures", "all"):
        collector = YFinanceFuturesCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["yfinance_futures"] = result
        print(f"[yfinance_futures]  {result}")

    if args.source in ("barchart_options",):
        collector = BarchartOptionsCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["barchart_options"] = result
        print(f"[barchart_options]  {result}")

    if args.source in ("etrade_options", "all"):
        collector = ETradeOptionsCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["etrade_options"] = result
        print(f"[etrade_options]    {result}")

    if args.source in ("yfinance_options",):
        collector = YFinanceOptionsCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["yfinance_options"] = result
        print(f"[yfinance_options]  {result}")

    if args.source in ("eia", "all"):
        collector = EIACollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["eia"] = result
        print(f"[eia]               {result}")

    # Legacy / CME direct (requires licensed access or session cookies — may 403)
    if args.source == "cme_futures":
        collector = CMEFuturesCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["cme_futures"] = result
        print(f"[cme_futures]       {result}")

    if args.source == "cme_options":
        collector = CMEOptionsCollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["cme_options"] = result
        print(f"[cme_options]       {result}")

    if args.source == "csv_futures":
        collector = CSVFuturesCollector(FIXTURES_DIR, raw_store, manifest_db)
        result = collector.collect(as_of)
        results["csv_futures"] = result
        print(f"[csv_futures]       {result}")

    any_failure = any(r.get("status") == "failed" for r in results.values())
    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()

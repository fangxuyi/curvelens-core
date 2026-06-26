#!/usr/bin/env python
"""
Normalization pipeline: raw → bronze → silver → quality report.

Usage:
    python scripts/normalize_day.py --date 2026-06-24

Steps:
  1. Find raw files for the date in the manifest DB
  2. Parse each into a bronze Parquet table
  3. Normalize bronze → silver (add calendar fields, validate, status)
  4. Persist bronze and silver Parquet to data/bronze/ and data/silver/
  5. Generate quality report to data/quality_reports/
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.parsers import bronze_eia, bronze_futures, bronze_options
from ccvm.normalizers import silver_futures, silver_options
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.parquet_store import ParquetStore
from ccvm.validation import quality_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MANIFEST_DB_PATH = DATA_DIR / "manifests" / "manifest.duckdb"
QUALITY_DIR = DATA_DIR / "quality_reports"


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize CCVM data for a given date")
    parser.add_argument("--date", required=True, help="Trade date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Re-normalize even if silver already exists")
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid date {args.date!r}")
        sys.exit(1)

    manifest_db = ManifestDB(MANIFEST_DB_PATH)
    pq_store = ParquetStore(DATA_DIR)
    as_of_str = args.date

    # Check if silver already exists
    if not args.force and pq_store.exists("silver", "futures", as_of_str):
        logger.info("Silver already exists for %s — use --force to re-normalize", as_of_str)

    # Fetch all raw manifest entries for this date
    all_entries = manifest_db.get_manifest_entries()
    date_entries = [e for e in all_entries if e.get("trade_date") == as_of_str]

    if not date_entries:
        logger.warning("No raw manifest entries for %s — run collect_day.py first", as_of_str)

    # --- Bronze + Silver: futures ---
    futures_entries = [e for e in date_entries if "futures" in e.get("source_id", "") and "option" not in e.get("source_id", "")]
    silver_fut_table = None
    for entry in futures_entries:
        raw_path = Path(entry["raw_path"])
        if not raw_path.exists():
            logger.warning("Raw file missing: %s", raw_path)
            continue
        sha256 = entry["sha256"]
        try:
            bronze_table = bronze_futures.parse(raw_path, sha256)
            logger.info("Bronze futures: %d rows from %s", len(bronze_table), raw_path.name)
            pq_store.write("bronze", "futures", as_of_str, bronze_table)

            silver_table = silver_futures.normalize(bronze_table, as_of)
            pq_store.write("silver", "futures", as_of_str, silver_table)
            silver_fut_table = silver_table
            logger.info("Silver futures: %d rows, statuses=%s",
                        len(silver_table),
                        dict(zip(*_count_status(silver_table))))
        except Exception as exc:
            logger.error("Failed to normalize futures %s: %s", raw_path.name, exc)

    # --- Bronze + Silver: options ---
    option_entries = [e for e in date_entries if "option" in e.get("source_id", "")]
    silver_opt_table = None
    for entry in option_entries:
        raw_path = Path(entry["raw_path"])
        if not raw_path.exists():
            logger.warning("Raw file missing: %s", raw_path)
            continue
        sha256 = entry["sha256"]
        try:
            bronze_table = bronze_options.parse(raw_path, sha256)
            logger.info("Bronze options: %d rows from %s", len(bronze_table), raw_path.name)
            pq_store.write("bronze", "options", as_of_str, bronze_table)

            silver_table = silver_options.normalize(bronze_table, as_of)
            pq_store.write("silver", "options", as_of_str, silver_table)
            silver_opt_table = silver_table
            logger.info("Silver options: %d rows, statuses=%s",
                        len(silver_table),
                        dict(zip(*_count_status(silver_table))))
        except Exception as exc:
            logger.error("Failed to normalize options %s: %s", raw_path.name, exc)

    # --- Bronze: EIA ---
    eia_entries = [e for e in date_entries if "eia" in e.get("source_id", "")]
    silver_eia_table = None
    for entry in eia_entries:
        raw_path = Path(entry["raw_path"])
        if not raw_path.exists():
            logger.warning("Raw file missing: %s", raw_path)
            continue
        sha256 = entry["sha256"]
        try:
            bronze_table = bronze_eia.parse(raw_path, sha256)
            logger.info("Bronze EIA: %d rows from %s", len(bronze_table), raw_path.name)
            pq_store.write("bronze", "eia", as_of_str, bronze_table)
            silver_eia_table = bronze_table  # EIA bronze is already normalized
        except Exception as exc:
            logger.error("Failed to parse EIA %s: %s", raw_path.name, exc)

    # --- Quality report ---
    report = quality_report.generate(
        trade_date=as_of,
        silver_futures=silver_fut_table,
        silver_options=silver_opt_table,
        silver_eia=silver_eia_table,
        output_dir=QUALITY_DIR,
    )
    logger.info("Quality report: overall=%s  futures=%s  options=%s  eia=%s",
                report["overall_status"],
                report["futures"]["status"],
                report["options"]["status"],
                report["fundamentals"]["status"])

    print(f"\nQuality report written to {QUALITY_DIR / as_of_str}.json")
    print(f"Overall status: {report['overall_status']}")
    sys.exit(0 if report["overall_status"] in ("PASS", "WARN", "INSUFFICIENT_DATA") else 1)


def _count_status(table) -> tuple[list, list]:
    """Return (statuses, counts) for printing."""
    import pyarrow.compute as pc
    if "silver_status" not in table.schema.names:
        return ([], [])
    vals = table.column("silver_status").to_pylist()
    from collections import Counter
    c = Counter(vals)
    return list(c.keys()), list(c.values())


if __name__ == "__main__":
    main()

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

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from ccvm.parsers import bronze_futures, bronze_options
from ccvm.normalizers import silver_futures, silver_options
from ccvm.fundamentals import get_provider
from ccvm.analytics.macro_context import normalize_fred
from ccvm.reference.product import get_product
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.parquet_store import ParquetStore
from ccvm.validation import quality_report
from ccvm.runtime import data_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = data_dir()
MANIFEST_DB_PATH = DATA_DIR / "manifests" / "manifest.duckdb"
QUALITY_DIR = DATA_DIR / "quality_reports"


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize CurveLens data for a given date")
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

    # --- Bronze + Silver + Gold: profile-selected fundamentals ---
    # E4: fundamentals stages come from the product profile registry
    provider = get_provider(get_product().fundamentals_provider)
    frag = provider.source_id_fragment if provider else "\x00none"
    eia_entries = [e for e in date_entries if provider and frag in e.get("source_id", "")]
    silver_eia_table = None
    bronze_eia_tables = []
    for entry in eia_entries:
        raw_path = Path(entry["raw_path"])
        if not raw_path.exists():
            logger.warning("Raw file missing: %s", raw_path)
            continue
        sha256 = entry["sha256"]
        try:
            bronze_table = provider.bronze.parse(raw_path, sha256)
            logger.info("Bronze fundamentals: %d rows from %s", len(bronze_table), raw_path.name)
            bronze_eia_tables.append(bronze_table)
        except Exception as exc:
            logger.error("Failed to parse fundamentals %s: %s", raw_path.name, exc)

    if bronze_eia_tables:
        import pyarrow as pa
        combined_bronze = pa.concat_tables(bronze_eia_tables)
        pq_store.write("bronze", "eia", as_of_str, combined_bronze)

        silver_eia_table = provider.silver.normalize(combined_bronze, as_of)
        pq_store.write("silver", "eia", as_of_str, silver_eia_table)
        pass_n = sum(1 for s in silver_eia_table.column("silver_status").to_pylist() if s == "PASS")
        logger.info("Silver fundamentals: %d rows, %d PASS", len(silver_eia_table), pass_n)

        gold_eia_table = provider.features.compute(silver_eia_table, as_of)
        pq_store.write("gold", "fundamentals_features", as_of_str, gold_eia_table)
        logger.info("Gold fundamentals: provider=%s rows=%d",
                    provider.name, len(gold_eia_table))

    # --- Silver: optional profile-driven FRED macro history ---
    product = get_product()
    macro_tables = []
    if product.macro and product.macro.provider == "fred":
        by_key = {s.key: s for s in product.macro.series}
        prefix = f"fred_{product.key}_macro_"
        for entry in date_entries:
            source_id = entry.get("source_id", "")
            if not source_id.startswith(prefix):
                continue
            key = source_id.removeprefix(prefix)
            spec = by_key.get(key)
            raw_path = Path(entry["raw_path"])
            if spec is None or not raw_path.exists():
                logger.warning("Unknown or missing macro raw entry: %s", source_id)
                continue
            try:
                table = normalize_fred(raw_path, entry["sha256"], spec, as_of)
                if len(table):
                    macro_tables.append(table)
            except Exception as exc:
                logger.error("Failed to normalize macro %s: %s", source_id, exc)
        if macro_tables:
            import pyarrow as pa
            silver_macro = pa.concat_tables(macro_tables)
            pq_store.write("silver", "macro", as_of_str, silver_macro)
            logger.info("Silver macro: %d observations across %d series",
                        len(silver_macro), len(macro_tables))

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

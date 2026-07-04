#!/usr/bin/env python
"""Print a lineage summary from the manifest database.

Usage:
    python scripts/check_lineage.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.storage.manifest_db import ManifestDB

PROJECT_ROOT = Path(__file__).parent.parent
MANIFEST_DB_PATH = PROJECT_ROOT / "data" / "manifests" / "manifest.duckdb"


def main() -> None:
    if not MANIFEST_DB_PATH.exists():
        print("No manifest database found. Run collect_day.py first.")
        sys.exit(0)

    db = ManifestDB(MANIFEST_DB_PATH)
    runs = db.get_run_history()
    entries = db.get_manifest_entries()

    print(f"\n{'=' * 60}")
    print(f"CurveLens Lineage Report — {MANIFEST_DB_PATH}")
    print(f"{'=' * 60}")
    print(f"\nCollection runs ({len(runs)} total):")
    print(f"{'Run ID':38} {'Source':30} {'Date':12} {'Status':10} {'S':>4} {'W':>4} {'F':>4} {'Skip':>4}")
    print("-" * 110)
    for r in runs:
        rid = str(r["run_id"])[:36]
        print(
            f"{rid:38} {r['source_id']:30} {r['as_of_date']:12} {r['status']:10} "
            f"{r['success_count']:>4} {r['warning_count']:>4} {r['failure_count']:>4} {r['skipped_count']:>4}"
        )

    print(f"\nManifest entries ({len(entries)} total):")
    print(f"{'SHA256':16} {'Source':30} {'Date':12} {'Bytes':>8} {'Filename'}")
    print("-" * 100)
    for e in entries:
        sha_short = str(e["sha256"])[:16]
        filename = Path(str(e["raw_path"])).name
        print(f"{sha_short:16} {e['source_id']:30} {str(e.get('trade_date', '')):12} {e['byte_size']:>8} {filename}")

    if db.has_duplicate_sha256():
        print("\n⚠ WARNING: Duplicate SHA-256 entries detected in manifest!")
        sys.exit(1)
    else:
        print(f"\n✓ No duplicate SHA-256 entries. Lineage is clean.")

    sys.exit(0)


if __name__ == "__main__":
    main()

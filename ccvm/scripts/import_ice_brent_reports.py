#!/usr/bin/env python
"""Import authorized ICE Report 10/166 CSV downloads for Brent."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.importers.ice_report_center import import_brent_reports
from ccvm.reference.product import get_product
from ccvm.runtime import data_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and import ICE Brent futures/options CSV reports",
    )
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--futures-csv", required=True, type=Path)
    parser.add_argument("--options-csv", required=True, type=Path)
    args = parser.parse_args()
    try:
        trade_date = date.fromisoformat(args.date)
        result = import_brent_reports(
            futures_csv=args.futures_csv,
            options_csv=args.options_csv,
            trade_date=trade_date,
            data_dir=data_dir(),
            product=get_product(),
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"result": "ERROR", "detail": str(exc)}))
        raise SystemExit(1)
    print(json.dumps({
        "result": "OK",
        "futures_path": str(result.futures_path),
        "options_path": str(result.options_path),
        "manifest_path": str(result.manifest_path),
        "futures_rows": result.futures_rows,
        "options_rows": result.options_rows,
    }))


if __name__ == "__main__":
    main()

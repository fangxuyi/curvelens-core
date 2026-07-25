#!/usr/bin/env python
"""Import authorized ICE Report 10/166 CSV or PDF downloads for Brent."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.importers.ice_report_center import (
    import_brent_pdf_directory,
    import_brent_pdf_reports,
    import_brent_reports,
)
from ccvm.reference.product import get_product
from ccvm.runtime import data_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and import ICE Brent futures/options reports",
    )
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    parser.add_argument("--futures-csv", type=Path)
    parser.add_argument("--options-csv", type=Path)
    parser.add_argument("--futures-pdf", type=Path)
    parser.add_argument("--options-pdf", type=Path)
    parser.add_argument(
        "--batch-pdf-root", type=Path,
        help="Directory containing futures/ and options/ PDF folders",
    )
    args = parser.parse_args()
    try:
        product = get_product()
        root = data_dir()
        if args.batch_pdf_root:
            if any((
                args.date, args.futures_csv, args.options_csv,
                args.futures_pdf, args.options_pdf,
            )):
                raise ValueError(
                    "--batch-pdf-root cannot be combined with date or file arguments"
                )
            results = import_brent_pdf_directory(
                batch_root=args.batch_pdf_root,
                data_dir=root,
                product=product,
            )
            print(json.dumps({
                "result": "OK",
                "mode": "batch_pdf",
                "imported_dates": [
                    item.futures_path.parent.name.removeprefix("trade_date=")
                    for item in results
                ],
                "dates_imported": len(results),
                "futures_rows": sum(item.futures_rows for item in results),
                "options_rows": sum(item.options_rows for item in results),
            }))
            return
        if not args.date:
            raise ValueError("--date is required for a CSV or PDF pair")
        trade_date = date.fromisoformat(args.date)
        if args.futures_csv or args.options_csv:
            if not args.futures_csv or not args.options_csv:
                raise ValueError(
                    "--futures-csv and --options-csv must be supplied together"
                )
            if args.futures_pdf or args.options_pdf:
                raise ValueError("choose either a CSV pair or a PDF pair")
            result = import_brent_reports(
                futures_csv=args.futures_csv,
                options_csv=args.options_csv,
                trade_date=trade_date,
                data_dir=root,
                product=product,
            )
            mode = "csv"
        elif args.futures_pdf or args.options_pdf:
            if not args.futures_pdf or not args.options_pdf:
                raise ValueError(
                    "--futures-pdf and --options-pdf must be supplied together"
                )
            result = import_brent_pdf_reports(
                futures_pdf=args.futures_pdf,
                options_pdf=args.options_pdf,
                trade_date=trade_date,
                data_dir=root,
                product=product,
            )
            mode = "pdf"
        else:
            raise ValueError("supply a CSV pair, PDF pair, or --batch-pdf-root")
    except (OSError, ValueError) as exc:
        print(json.dumps({"result": "ERROR", "detail": str(exc)}))
        raise SystemExit(1)
    print(json.dumps({
        "result": "OK",
        "mode": mode,
        "futures_path": str(result.futures_path),
        "options_path": str(result.options_path),
        "manifest_path": str(result.manifest_path),
        "futures_rows": result.futures_rows,
        "options_rows": result.options_rows,
    }))


if __name__ == "__main__":
    main()

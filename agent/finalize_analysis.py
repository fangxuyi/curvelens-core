#!/usr/bin/env python
"""Validate specialist/synthesis responses and render the daily analysis."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CCVM_DIR = REPO_ROOT / "ccvm"
sys.path.insert(0, str(CCVM_DIR / "src"))

from ccvm.runtime import data_dir
from ccvm.workflow.finalize import AnalysisValidationError, validate_and_render


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    root = data_dir() / "analysis_workflow" / f"trade_date={args.date}"
    try:
        json_path, md_path, statistics_path = validate_and_render(
            root / "manifest.json",
            data_dir() / "analysis" / f"trade_date={args.date}",
        )
    except (AnalysisValidationError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "INVALID_AGENT_OUTPUT", "detail": str(exc)}))
        raise SystemExit(1)
    print(json.dumps({
        "result": "SHADOW_ANALYSIS_READY", "date": args.date,
        "analysis_json": str(json_path), "analysis_md": str(md_path),
        "statistics_md": str(statistics_path),
        "delivery_queued": False,
    }))


if __name__ == "__main__":
    main()

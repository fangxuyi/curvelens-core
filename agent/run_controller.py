#!/usr/bin/env python
"""Restricted host launcher for the native daily controller, never a pipeline."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ccvm/src"))
from ccvm.reference.product import load_product

DAILY = {"start", "advance", "inspect", "status"}
COMMANDS = DAILY | {"learn", "retrospect"}


def validated_arguments(args: list[str], product: str | None) -> list[str]:
    if not product:
        raise ValueError("Explicit CCVM_PRODUCT required")
    load_product(product)
    if len(args) not in {3, 5} or args[0] not in COMMANDS or args[1] != "--date":
        raise ValueError("Use a supported controller command --date YYYY-MM-DD")
    dates = [args[2]]
    if len(args) == 5:
        if args[0] not in DAILY or args[3] != "--expected-date":
            raise ValueError("--expected-date is only allowed on daily workflow commands")
        dates.append(args[4])
    for value in dates:
        if datetime.date.fromisoformat(value).isoformat() != value:
            raise ValueError("Dates must be YYYY-MM-DD")
    return args


def main() -> int:
    try:
        args = validated_arguments(sys.argv[1:], os.environ.get("CCVM_PRODUCT"))
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"result": "INVALID_LAUNCH_ARGUMENTS", "detail": str(exc)}))
        return 2
    if os.environ.get("CODEX_SANDBOX") == "seatbelt":
        print(json.dumps({"result": "PROVIDER_PERMISSION_REQUIRED", "detail":
                         "Use the approved outside-sandbox launcher; provider network and cache access are required."}))
        return 1
    return subprocess.call(
        [sys.executable, str(ROOT / "agent/analysis_orchestrator.py"), *args], cwd=ROOT,
    )


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Retired direct-model entry point retained for a clear migration error."""
from __future__ import annotations

import json


def main() -> None:
    print(json.dumps({
        "result": "RETIRED",
        "detail": (
            "Direct catalyst extraction is disabled. Use "
            "agent/run_analysis_workflow.py; model analysis must be delegated "
            "through the host agent framework."
        ),
    }))
    raise SystemExit(2)


if __name__ == "__main__":
    main()

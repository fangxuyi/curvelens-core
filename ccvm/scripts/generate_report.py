#!/usr/bin/env python
"""
Generate the daily forward-risk brief.

Usage:
    python scripts/generate_report.py --date 2026-06-25

Reads gold-layer features, agreement classification, and catalyst events,
then writes:
    data/reports/YYYY-MM-DD.md
    data/reports/YYYY-MM-DD.json

Must run after:
    python scripts/collect_day.py --date 2026-06-25 --source all
    python scripts/normalize_day.py --date 2026-06-25
    python scripts/compute_features.py --date 2026-06-25
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.agents.catalyst_store import CatalystStore
from ccvm.scenarios.scenario_engine import generate as gen_scenarios, to_dict
from ccvm.reporting.daily_report import generate as gen_report
from ccvm.storage.parquet_store import ParquetStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid date {args.date!r}")
        sys.exit(1)

    pq = ParquetStore(DATA_DIR)
    as_of_str = args.date

    # ── Gold futures ──
    if not pq.exists("gold", "futures_features", as_of_str):
        logger.error("No gold futures features for %s", as_of_str)
        sys.exit(1)

    gold_fut = pq.read("gold", "futures_features", as_of_str)

    # ── Gold options (optional) ──
    gold_opt = None
    if pq.exists("gold", "option_features", as_of_str):
        gold_opt = pq.read("gold", "option_features", as_of_str)

    # ── Gold EIA features (optional) ──
    gold_eia = None
    if pq.exists("gold", "eia_features", as_of_str):
        gold_eia = pq.read("gold", "eia_features", as_of_str)

    # ── History context (optional) ──
    history_ctx = None
    if pq.exists("gold", "history_context", as_of_str):
        history_ctx = pq.read("gold", "history_context", as_of_str)

    # ── Agreement ──
    agr_path = DATA_DIR / "gold" / "agreement" / f"trade_date={as_of_str}" / "agreement.json"
    if agr_path.exists():
        agreement = json.loads(agr_path.read_text())
    else:
        logger.warning("No agreement classification for %s", as_of_str)
        agreement = {"state": "insufficient_data", "confidence": "low", "evidence": []}

    # ── Quality report ──
    quality_path = DATA_DIR / "quality_reports" / f"{as_of_str}.json"
    quality_report = {}
    if quality_path.exists():
        quality_report = json.loads(quality_path.read_text())

    # ── Catalysts ──
    store = CatalystStore(DATA_DIR)
    catalysts = store.load(as_of)
    catalysts.sort(key=lambda e: e.get("relevance_score", 0), reverse=True)

    # ── Scenarios ──
    scenarios = gen_scenarios(gold_fut, gold_opt, as_of)
    scenarios_dict = [to_dict(s) for s in scenarios]

    # ── Report ──
    output_dir = DATA_DIR / "reports"
    report = gen_report(
        trade_date=as_of,
        gold_futures=gold_fut,
        gold_options=gold_opt,
        scenarios=scenarios_dict,
        agreement=agreement,
        top_catalysts=catalysts,
        quality_report=quality_report,
        output_dir=output_dir,
        gold_eia=gold_eia,
        history_context=history_ctx,
    )

    md_path = output_dir / f"{as_of_str}.md"
    json_path = output_dir / f"{as_of_str}.json"
    logger.info("Report written to %s", md_path)
    logger.info("JSON report written to %s", json_path)

    print(f"\n{'='*60}")
    print(f"CurveLens Daily Brief — {as_of_str}")
    print(f"Quality: {report['quality_status']}")
    print(f"Agreement: {agreement.get('state')} ({agreement.get('confidence')})")
    print(f"Catalysts: {len(catalysts)} events")
    print(f"Report: {md_path}")
    print(f"{'='*60}\n")

    # Print brief MD to stdout
    print(md_path.read_text())


if __name__ == "__main__":
    main()

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
from ccvm.runtime import data_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = data_dir()


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
    fundamentals_dataset = ("fundamentals_features"
                            if pq.exists("gold", "fundamentals_features", as_of_str)
                            else "eia_features")
    if pq.exists("gold", fundamentals_dataset, as_of_str):
        gold_eia = pq.read("gold", fundamentals_dataset, as_of_str)

    # ── History context (optional) ──
    history_ctx = None
    if pq.exists("gold", "history_context", as_of_str):
        history_ctx = pq.read("gold", "history_context", as_of_str)

    # ── Monitor: trigger eval, scenario state, streaks, day diff (C1/D2/D3) ──
    from ccvm.analytics import monitor_state
    trig_path = DATA_DIR / "gold" / "triggers" / f"trade_date={as_of_str}" / "triggers.json"
    monitor = json.loads(trig_path.read_text()) if trig_path.exists() else None
    streaks = monitor_state.compute_streaks(pq, DATA_DIR, as_of_str)
    day_diff = monitor_state.build_day_diff(pq, DATA_DIR, as_of_str)

    # ── Calibration scorecard (C7) ──
    from ccvm.analytics import scorecard as scorecard_mod
    scorecard = scorecard_mod.compute(pq, DATA_DIR, as_of_str)

    # ── OI analytics (C2, optional) ──
    oi_path = DATA_DIR / "gold" / "oi" / f"trade_date={as_of_str}" / "oi.json"
    oi = json.loads(oi_path.read_text()) if oi_path.exists() else None

    # ── COT positioning (B3, optional) ──
    cot_path = DATA_DIR / "gold" / "cot" / f"trade_date={as_of_str}" / "cot.json"
    cot = json.loads(cot_path.read_text()) if cot_path.exists() else None

    # ── RND (C3, optional) ──
    rnd_path = DATA_DIR / "gold" / "rnd" / f"trade_date={as_of_str}" / "rnd.json"
    rnd_ctx = json.loads(rnd_path.read_text()) if rnd_path.exists() else None

    # ── EIA seasonal (B4, optional) ──
    seas_path = DATA_DIR / "gold" / "eia_seasonal" / f"trade_date={as_of_str}" / "seasonal.json"
    eia_seasonal_ctx = json.loads(seas_path.read_text()) if seas_path.exists() else None

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

    # ── C5: dedup near-identical stories, decay past events, cluster themes ──
    from ccvm.agents.catalyst_dedup import apply_decay, cluster_themes, dedupe
    catalysts = apply_decay(dedupe(catalysts), as_of)
    themes = cluster_themes(catalysts)

    # ── Scenarios ──
    # ── C6: top catalysts feed the event-scenario slot ──
    from ccvm.scenarios.scenario_engine import event_shocks_from_catalysts
    extra = event_shocks_from_catalysts(catalysts)
    scenarios = gen_scenarios(gold_fut, gold_opt, as_of, extra_shocks=extra or None)
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
        monitor=monitor,
        streaks=streaks,
        day_diff=day_diff,
        oi=oi,
        cot=cot,
        eia_seasonal=eia_seasonal_ctx,
        rnd=rnd_ctx,
        themes=themes,
        scorecard=scorecard,
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

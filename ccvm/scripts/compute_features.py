#!/usr/bin/env python
"""
Feature computation pipeline: silver → gold.

Usage:
    python scripts/compute_features.py --date 2026-06-24

Reads silver Parquet for the given date, computes:
  - Futures curve features (gold/futures_features/)
  - Options surface features with Black-76 IV (gold/option_features/)
  - Agreement classification
Writes all to gold Parquet + prints a summary.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.analytics import futures_features, option_features, agreement
from ccvm.validation.quality_report import delta_check_section
from ccvm.storage.parquet_store import ParquetStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--prior-date", help="Prior trade date for returns/IV change")
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid date {args.date!r}")
        sys.exit(1)

    pq = ParquetStore(DATA_DIR)
    as_of_str = args.date

    # ── Silver futures ──
    if not pq.exists("silver", "futures", as_of_str):
        logger.error("No silver futures for %s — run normalize_day.py first", as_of_str)
        sys.exit(1)

    silver_fut = pq.read("silver", "futures", as_of_str)
    prior_fut = None
    if args.prior_date and pq.exists("silver", "futures", args.prior_date):
        prior_fut = pq.read("silver", "futures", args.prior_date)

    # ── Futures features ──
    gold_fut = futures_features.compute(silver_fut, as_of, prior_fut)
    pq.write("gold", "futures_features", as_of_str, gold_fut)
    logger.info("Gold futures features: %d contracts", len(gold_fut))

    # Print curve summary
    d = gold_fut.to_pydict()
    if d["contract_code"]:
        front_settle = d["settlement"][0]
        slope = d["front_back_slope"][0]
        contango = d["contango_flag"][0]
        logger.info("  Front: %s  settle=%.2f  slope=%.3f/mo  %s",
                    d["contract_code"][0], front_settle, slope,
                    "CONTANGO" if contango else "BACKWARDATION")

    # ── Silver options ──
    silver_opt = None
    gold_opt = None
    if pq.exists("silver", "options", as_of_str):
        silver_opt = pq.read("silver", "options", as_of_str)
        gold_opt = option_features.compute(silver_opt, silver_fut, as_of)
        if len(gold_opt) > 0:
            pq.write("gold", "option_features", as_of_str, gold_opt)
            logger.info("Gold option features: %d rows", len(gold_opt))
            od = gold_opt.to_pydict()
            ivs = [v for v in od["black76_iv"] if v is not None]
            if ivs:
                logger.info("  IV range: %.1f%% – %.1f%%  ATM: %.1f%%",
                            min(ivs) * 100, max(ivs) * 100,
                            (od["atm_iv"][0] or 0) * 100)

            # ── Delta quality check ──
            dc = delta_check_section(gold_opt)
            logger.info(
                "Delta check: status=%s  compared=%d  mean|Δ|=%.4f  max|Δ|=%.4f",
                dc["status"], dc.get("compared", 0),
                dc.get("mean_abs_delta_diff", 0), dc.get("max_abs_delta_diff", 0),
            )
            for note in dc.get("notes", []):
                logger.warning("  delta_check: %s", note)

            quality_path = DATA_DIR / "quality_reports" / f"{as_of_str}.json"
            if quality_path.exists():
                quality = json.loads(quality_path.read_text())
                quality["delta_check"] = dc
                quality_path.write_text(json.dumps(quality, indent=2))
        else:
            logger.warning("No valid option features computed")
    else:
        logger.info("No silver options for %s — skipping option features", as_of_str)

    # ── Agreement classification ──
    slope_val = d["front_back_slope"][0] if d["front_back_slope"] else None
    contango_val = d["contango_flag"][0] if d["contango_flag"] else None

    rr25 = None
    atm_iv = None
    if gold_opt and len(gold_opt) > 0:
        od = gold_opt.to_pydict()
        rr25_vals = [v for v in od["risk_reversal_25d"] if v is not None]
        atm_vals = [v for v in od["atm_iv"] if v is not None]
        rr25 = rr25_vals[0] if rr25_vals else None
        atm_iv = atm_vals[0] if atm_vals else None

    prior_atm_iv = None
    prior_slope = None
    if prior_fut is not None and args.prior_date:
        pf_d = prior_fut.to_pydict()
        if pf_d.get("silver_status"):
            prior_settles = [s for s, st in zip(pf_d["settlement"], pf_d["silver_status"]) if st != "FAIL"]
            if len(prior_settles) >= 2:
                prior_slope = (prior_settles[-1] - prior_settles[0]) / max(len(prior_settles) - 1, 1)
        if args.prior_date and pq.exists("gold", "option_features", args.prior_date):
            prior_gold_opt = pq.read("gold", "option_features", args.prior_date)
            po_d = prior_gold_opt.to_pydict()
            prior_atm_vals = [v for v in po_d.get("atm_iv", []) if v is not None]
            prior_atm_iv = prior_atm_vals[0] if prior_atm_vals else None

    agr = agreement.classify(
        front_back_slope=slope_val,
        contango_flag=contango_val,
        risk_reversal_25d=rr25,
        atm_iv=atm_iv,
        prior_atm_iv=prior_atm_iv,
        prior_slope=prior_slope,
    )
    logger.info("Agreement state: %s (%s)", agr["state"], agr["confidence"])
    for ev in agr["evidence"]:
        logger.info("  • %s", ev)

    # Save agreement to gold
    agr_path = DATA_DIR / "gold" / "agreement" / f"trade_date={as_of_str}" / "agreement.json"
    agr_path.parent.mkdir(parents=True, exist_ok=True)
    agr_path.write_text(json.dumps({**agr, "trade_date": as_of_str}, indent=2))

    print(f"\nAgreement state: {agr['state']} (confidence: {agr['confidence']})")
    print(f"Gold features written to {DATA_DIR}/gold/")


if __name__ == "__main__":
    main()

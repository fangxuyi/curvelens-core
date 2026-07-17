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

from ccvm.analytics import (
    agreement,
    cot_features,
    eia_seasonal,
    rnd,
    futures_features,
    history_context,
    monitor_state,
    oi_features,
    option_features,
)
from ccvm.validation.quality_report import delta_check_section
from ccvm.storage.parquet_store import ParquetStore
from ccvm.runtime import data_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = data_dir()


def _fmt_pct(v) -> str:
    return f"{v:.0f}" if v is not None else "n/a"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--prior-date", help="Prior trade date for returns/IV change (inferred if omitted)")
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid date {args.date!r}")
        sys.exit(1)

    pq = ParquetStore(DATA_DIR)
    as_of_str = args.date

    # Infer prior date from available silver futures if not supplied
    if not args.prior_date:
        available = pq.list_dates("silver", "futures")
        earlier = [d for d in available if d < as_of_str]
        args.prior_date = earlier[-1] if earlier else None
        if args.prior_date:
            logger.info("Inferred prior date: %s", args.prior_date)

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

    # ── Open-interest analytics (C2) ──
    if silver_opt is not None:
        prior_silver_opt = None
        if args.prior_date and pq.exists("silver", "options", args.prior_date):
            prior_silver_opt = pq.read("silver", "options", args.prior_date)
        oi = oi_features.compute(silver_opt, as_of_str, prior_silver_opt)
        oi_path = DATA_DIR / "gold" / "oi" / f"trade_date={as_of_str}" / "oi.json"
        oi_path.parent.mkdir(parents=True, exist_ok=True)
        oi_path.write_text(json.dumps(oi, indent=2))
        if oi["expiries"]:
            e0 = oi["expiries"][0]
            logger.info(
                "OI (front %s): P/C=%s  max_pain=%s  top call wall=%s",
                e0["expiry"], e0["put_call_oi_ratio"], e0["max_pain"],
                (e0["call_walls"][0]["strike"] if e0["call_walls"] else None),
            )

    # ── Risk-neutral density (C3) ──
    if gold_opt is not None and len(gold_opt) > 0:
        rnd_out = rnd.compute(gold_opt, as_of_str)
        if rnd_out["expiries"]:
            rnd_path = DATA_DIR / "gold" / "rnd" / f"trade_date={as_of_str}" / "rnd.json"
            rnd_path.parent.mkdir(parents=True, exist_ok=True)
            rnd_path.write_text(json.dumps(rnd_out, indent=2))
            e0 = rnd_out["expiries"][0]
            em = e0.get("expected_move_straddle")
            logger.info("RND (front %s): expected move %s  RN sigma %.2f  skew %+.2f  mass %.2f",
                        e0["expiry"], f"+/-{em:.2f}" if em else "n/a",
                        e0["rn_std"], e0["rn_skew"], e0["raw_mass"])

    # ── COT positioning context (B3) ──
    cot = cot_features.compute(DATA_DIR, as_of_str)
    if cot is not None:
        cot_path = DATA_DIR / "gold" / "cot" / f"trade_date={as_of_str}" / "cot.json"
        cot_path.parent.mkdir(parents=True, exist_ok=True)
        cot_path.write_text(json.dumps(cot, indent=2))
        wow = cot["mm_net_wow"]
        logger.info("COT (report %s): MM net %+d  WoW %s  1y %s%%ile",
                    cot["report_date"], cot["mm_net"],
                    f"{wow:+d}" if wow is not None else "n/a",
                    _fmt_pct(cot["mm_net_pctile_1y"]))

    # ── History context: percentiles / z-scores vs accumulated gold ──
    ctx = history_context.compute(pq, as_of_str)
    if ctx is not None:
        pq.write("gold", "history_context", as_of_str, ctx)
        cd = ctx.to_pydict()
        logger.info(
            "History context (%dd): ATM IV %s%%ile  RR25 %s%%ile  slope %s%%ile",
            cd["lookback_days"][0],
            _fmt_pct(cd["atm_iv_pctile"][0]),
            _fmt_pct(cd["rr25_pctile"][0]),
            _fmt_pct(cd["curve_slope_pctile"][0]),
        )

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

    # ── EIA supply signal (optional) ──
    eia_supply_signal = None
    eia_scenario_trigger = None
    fundamentals_dataset = ("fundamentals_features"
                            if pq.exists("gold", "fundamentals_features", as_of_str)
                            else "eia_features")
    if pq.exists("gold", fundamentals_dataset, as_of_str):
        gold_eia = pq.read("gold", fundamentals_dataset, as_of_str)
        ed = gold_eia.to_pydict()
        eia_supply_signal = ed["supply_signal"][0] if ed["supply_signal"] else None
        eia_scenario_trigger = ed["scenario_trigger"][0] if ed["scenario_trigger"] else None
        logger.info("EIA supply signal: %s  trigger: %s", eia_supply_signal, eia_scenario_trigger)

    # ── Seasonal EIA trigger (B4): surprise vs 5y week-of-year norm ──
    seasonal = eia_seasonal.compute(DATA_DIR, as_of_str)
    if seasonal is not None:
        seas_path = DATA_DIR / "gold" / "eia_seasonal" / f"trade_date={as_of_str}" / "seasonal.json"
        seas_path.parent.mkdir(parents=True, exist_ok=True)
        seas_path.write_text(json.dumps(seasonal, indent=2))
        if seasonal.get("seasonal_available"):
            logger.info(
                "EIA seasonal: draw %.0f vs 5y-avg draw %.0f → surprise %.0f MBBL  trigger=%s%s",
                seasonal["actual_draw_mbbl"], seasonal["seasonal_avg_draw_mbbl"],
                seasonal["surprise_draw_mbbl"], seasonal["trigger"],
                "  (DISAGREES with fixed)" if seasonal.get("disagrees_with_fixed") else "",
            )
            eia_scenario_trigger = seasonal["trigger"]

    agr = agreement.classify(
        front_back_slope=slope_val,
        front_settlement=d["settlement"][0] if d.get("settlement") else None,
        contango_flag=contango_val,
        risk_reversal_25d=rr25,
        atm_iv=atm_iv,
        prior_atm_iv=prior_atm_iv,
        prior_slope=prior_slope,
        eia_supply_signal=eia_supply_signal,
        eia_scenario_trigger=eia_scenario_trigger,
    )
    logger.info("Agreement state: %s (%s)", agr["state"], agr["confidence"])
    for ev in agr["evidence"]:
        logger.info("  • %s", ev)

    # Save agreement to gold
    agr_path = DATA_DIR / "gold" / "agreement" / f"trade_date={as_of_str}" / "agreement.json"
    agr_path.parent.mkdir(parents=True, exist_ok=True)
    agr_path.write_text(json.dumps({**agr, "trade_date": as_of_str}, indent=2))

    # ── Trigger evaluation + scenario state machine (C1) ──
    monitor = monitor_state.update_scenario_state(pq, DATA_DIR, as_of_str)
    trig_path = DATA_DIR / "gold" / "triggers" / f"trade_date={as_of_str}" / "triggers.json"
    trig_path.parent.mkdir(parents=True, exist_ok=True)
    trig_path.write_text(json.dumps(monitor, indent=2))
    for sc, st in monitor["scenarios"].items():
        logger.info(
            "Scenario %s: %s (since %s)  confirms=%d/%d  invalidations=%s",
            sc.upper(), st["status"].upper(), st["since"],
            len(st["confirms_fired"]), st["confirms_total_auto"],
            st["invalidations_fired"] or "none",
        )

    print(f"\nAgreement state: {agr['state']} (confidence: {agr['confidence']})")
    print(f"Gold features written to {DATA_DIR}/gold/")


if __name__ == "__main__":
    main()

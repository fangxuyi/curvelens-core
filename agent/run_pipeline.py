#!/usr/bin/env python
"""CurveLens end-to-end daily pipeline — single entry point for the monitor agent.

Runs the full 5-stage pipeline for one trade date and prints a single line of
JSON to stdout describing the outcome. Designed to be invoked as one tool call
by the standalone CurveLens agent (see AGENTS.md Runtime Model), rather than
having the agent orchestrate five separate scripts from a prompt.

Stages (each an isolated subprocess of the current interpreter):
    1. collect_day.py       raw ingest (yfinance futures, CME PDF, EIA, RSS)
    2. normalize_day.py     raw → bronze → silver + quality report
    3. compute_features.py  silver → gold (curve, BAW vol surface, agreement)
    4. extract_catalysts.py RSS articles → ranked catalyst events (needs claude CLI)
    5. generate_report.py   gold → data/reports/<date>.md + .json

Usage:
    python agent/run_pipeline.py                 # today (America/New_York)
    python agent/run_pipeline.py --date 2026-07-02
    python agent/run_pipeline.py --date 2026-07-02 --skip-catalysts

stdout is exactly one JSON object. Human-readable progress goes to stderr.

Result shapes:
    {"result": "NEED_CME_PDF", "date": ..., "pdf_path": ..., "url": ...}
        The configured CME option bulletin for this date is not on disk. The agent must
        download it (CME is Akamai bot-protected, so the deterministic pipeline
        cannot fetch it itself) to pdf_path, then re-run.
    {"result": "OK", "date": ..., "report_md": ..., "report_json": ...,
     "agreement_state": ..., "confidence": ..., "eia_scenario": ...,
     "alert_worthy": true|false, "headline": ...}
    {"result": "ERROR", "step": ..., "detail": ...}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# This script lives in CurveLens/agent/; the deterministic pipeline and its
# data live one level down in CurveLens/ccvm/.
REPO_ROOT = Path(__file__).parent.parent
CCVM_DIR = REPO_ROOT / "ccvm"
SCRIPTS = CCVM_DIR / "scripts"

sys.path.insert(0, str(CCVM_DIR / "src"))
from ccvm.runtime import data_dir

DATA_DIR = data_dir()

# Agreement states / EIA scenarios that warrant an immediate priority alert
# rather than just the routine daily brief.
_ALERT_STATES = {"confirmed_upside_risk", "confirmed_downside_risk"}
_ALERT_SCENARIOS = {"bull_confirmed", "bear_confirmed"}

def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _emit(obj: dict) -> None:
    """Print the single machine-readable result line to stdout and exit."""
    print(json.dumps(obj))
    sys.exit(0 if obj.get("result") in ("OK", "NEED_CME_PDF") else 1)


def _ny_today() -> date:
    """Current date in the deployment's operating timezone."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def _run_stage(name: str, argv: list[str], required: bool) -> bool:
    """Run a pipeline stage as a subprocess. Returns True on success.

    If a required stage fails, emits an ERROR result and exits. Optional stages
    log a warning and continue.
    """
    _eprint(f"\n── stage: {name} ──")
    # Redirect the child's stdout to our stderr so this script's stdout stays
    # clean for the single machine-readable JSON result line.
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / argv[0]), *argv[1:]],
        cwd=str(CCVM_DIR),
        stdout=sys.stderr,
    )
    if proc.returncode != 0:
        if required:
            _emit({
                "result": "ERROR",
                "step": name,
                "detail": f"{argv[0]} exited with code {proc.returncode}",
            })
        _eprint(f"⚠ optional stage '{name}' failed (exit {proc.returncode}) — continuing")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full CurveLens daily pipeline")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today, America/New_York)")
    parser.add_argument("--skip-catalysts", action="store_true",
                        help="Skip the claude-CLI catalyst extraction stage")
    parser.add_argument("--force-pdf", action="store_true",
                        help="Proceed even if the CME option bulletin PDF is missing")
    args = parser.parse_args()

    if args.date:
        try:
            as_of = date.fromisoformat(args.date)
        except ValueError:
            _emit({"result": "ERROR", "step": "args", "detail": f"invalid date {args.date!r}"})
    else:
        as_of = _ny_today()
    as_of_str = as_of.isoformat()
    _eprint(f"CurveLens pipeline — trade date {as_of_str}")

    # ── Pre-flight: bulletin-backed products require the PDF on disk. ──
    from ccvm.reference.product import get_product
    product = get_product()
    pdf_path = DATA_DIR / "cme_bulletin" / f"{as_of_str}.pdf"
    if product.bulletin and not pdf_path.exists() and not args.force_pdf:
        _emit({
            "result": "NEED_CME_PDF",
            "date": as_of_str,
            "pdf_path": str(pdf_path),
            "url": product.bulletin.url,
            "detail": ("Download the product profile's configured CME bulletin "
                       "for this date and save it to pdf_path, then re-run."),
        })

    # ── Stage 1: collect (futures + CME PDF + optional fundamentals + RSS) ──
    _run_stage("collect", ["collect_day.py", "--date", as_of_str, "--source", "all"],
               required=True)

    # ── Stage 2: normalize ──
    _run_stage("normalize", ["normalize_day.py", "--date", as_of_str], required=True)

    # ── Stage 3: compute features ──
    _run_stage("compute", ["compute_features.py", "--date", as_of_str], required=True)

    # ── Stage 4: catalyst extraction (optional; needs claude CLI + RSS) ──
    if not args.skip_catalysts:
        _run_stage("extract_catalysts", ["extract_catalysts.py", "--date", as_of_str],
                   required=False)
    else:
        _eprint("\n── stage: extract_catalysts (skipped) ──")

    # ── Stage 5: generate report ──
    _run_stage("report", ["generate_report.py", "--date", as_of_str], required=True)

    # ── Summarize outcome from the gold layer ──
    summary = _build_summary(as_of, as_of_str)
    _emit(summary)


def _build_summary(as_of: date, as_of_str: str) -> dict:
    """Read gold/report artifacts and build the OK result object."""
    from ccvm.storage.parquet_store import ParquetStore

    pq = ParquetStore(DATA_DIR)

    report_md = DATA_DIR / "reports" / f"{as_of_str}.md"
    report_json = DATA_DIR / "reports" / f"{as_of_str}.json"

    # Agreement classification
    agr_path = DATA_DIR / "gold" / "agreement" / f"trade_date={as_of_str}" / "agreement.json"
    agreement_state, confidence = "insufficient_data", "low"
    if agr_path.exists():
        agr = json.loads(agr_path.read_text())
        agreement_state = agr.get("state", agreement_state)
        confidence = agr.get("confidence", confidence)

    # EIA scenario trigger — prefer the seasonally-adjusted trigger (B4)
    # over the fixed-threshold one; fall back to gold eia_features.
    eia_scenario = "none"
    fundamentals_dataset = ("fundamentals_features"
                            if pq.exists("gold", "fundamentals_features", as_of_str)
                            else "eia_features")
    seas_path = DATA_DIR / "gold" / "eia_seasonal" / f"trade_date={as_of_str}" / "seasonal.json"
    if seas_path.exists():
        eia_scenario = json.loads(seas_path.read_text()).get("trigger", "none") or "none"
    if eia_scenario == "none" and pq.exists("gold", fundamentals_dataset, as_of_str):
        ed = pq.read("gold", fundamentals_dataset, as_of_str).to_pydict()
        eia_scenario = (ed.get("scenario_trigger") or ["none"])[0] or "none"

    # Front contract headline
    headline = ""
    if pq.exists("gold", "futures_features", as_of_str):
        fd = pq.read("gold", "futures_features", as_of_str).to_pydict()
        if fd.get("contract_code"):
            code = fd["contract_code"][0]
            settle = fd["settlement"][0]
            ret = (fd.get("return_1d") or [None])[0]
            ret_str = f" ({ret*100:+.2f}% 1d)" if ret is not None else ""
            from ccvm.reference.product import get_product
            product = get_product()
            headline = f"{code} @ {settle:.2f} {product.price_unit}{ret_str}"

    alert_worthy = (agreement_state in _ALERT_STATES) or (eia_scenario in _ALERT_SCENARIOS)

    return {
        "result": "OK",
        "date": as_of_str,
        "report_md": str(report_md) if report_md.exists() else None,
        "report_json": str(report_json) if report_json.exists() else None,
        "agreement_state": agreement_state,
        "confidence": confidence,
        "eia_scenario": eia_scenario,
        "alert_worthy": alert_worthy,
        "headline": headline,
    }


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Event-calendar runs (D1) — same-day mini-runs, no bulletin needed.

Two deployment-specific modes, scheduled from the active knowledge-pack
calendar (see the selected `deployments/<product>/` runbook):

  --event eia   Wed ~10:35 ET, right after the EIA release. Collects the fresh
                EIA data, computes the seasonal surprise, and queues an
                EIA_FLASH message ONLY when the seasonally-adjusted trigger
                fires (bull_confirmed / bear_confirmed). Hours earlier than the
                T+1 settlement brief.
  --event cot   Fri ~15:35 ET, after the CFTC release. Collects the fresh COT
                data and queues a short COT_UPDATE ONLY when positioning moved
                materially (|WoW| ≥ 20k lots or 1y percentile ≥90 / ≤10).

stdout is one JSON line; quiet outcomes are the norm. Delivery stays the
agent's job (notify list-pending → send verbatim → ack), same as the daily run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
CCVM_DIR = REPO_ROOT / "ccvm"
sys.path.insert(0, str(CCVM_DIR / "src"))
sys.path.insert(0, str(REPO_ROOT / "agent"))
from ccvm.runtime import data_dir

DATA_DIR = data_dir()

_COT_WOW_THRESHOLD = 20_000
_COT_PCTILE_HI, _COT_PCTILE_LO = 90.0, 10.0


def _emit(obj: dict) -> None:
    print(json.dumps(obj))
    sys.exit(0 if obj.get("result") != "ERROR" else 1)


def _ny_today() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _collect(source: str, as_of: str) -> bool:
    proc = subprocess.run(
        [sys.executable, str(CCVM_DIR / "scripts" / "collect_day.py"),
         "--date", as_of, "--source", source],
        cwd=str(CCVM_DIR), stdout=sys.stderr,
    )
    return proc.returncode == 0


def run_eia(as_of: str) -> dict:
    from ccvm.analytics import eia_seasonal
    from notify import queue_message

    if not _collect("eia", as_of):
        return {"result": "ERROR", "event": "eia", "detail": "EIA collect failed"}

    seasonal = eia_seasonal.compute(DATA_DIR, as_of)
    if seasonal is None:
        return {"result": "NO_DATA", "event": "eia", "date": as_of}

    trigger = seasonal.get("trigger", "none")
    if trigger not in ("bull_confirmed", "bear_confirmed"):
        return {"result": "QUIET", "event": "eia", "date": as_of,
                "trigger": trigger,
                "surprise_draw_mbbl": seasonal.get("surprise_draw_mbbl")}

    direction = "BULLISH" if trigger == "bull_confirmed" else "BEARISH"
    surp = seasonal.get("surprise_draw_mbbl")
    surp_str = f"{surp:+,.0f} MBBL vs 5-yr seasonal norm" if surp is not None else "n/a"
    text = "\n".join([
        f"⚡ *CurveLens EIA FLASH — {direction}*",
        f"_{as_of}_ · week ending {seasonal.get('eia_period')}",
        "",
        f"*Crude (ex-SPR):* {'draw' if seasonal['actual_draw_mbbl'] > 0 else 'build'} "
        f"of {abs(seasonal['actual_draw_mbbl']):,.0f} MBBL",
        f"*Seasonal surprise:* {surp_str}",
        f"*Trigger:* `{trigger}` (seasonally adjusted)",
        "",
        "Full analysis in tomorrow's settlement brief.",
    ])
    q = queue_message("EIA_FLASH", as_of, text)
    return {"result": "FLASH_QUEUED" if q["result"] == "QUEUED" else "ALREADY_HANDLED",
            "event": "eia", "date": as_of, "trigger": trigger, "queue": q}


def run_cot(as_of: str) -> dict:
    from ccvm.analytics import cot_features
    from notify import queue_message

    if not _collect("cftc_cot", as_of):
        return {"result": "ERROR", "event": "cot", "detail": "COT collect failed"}

    cot = cot_features.compute(DATA_DIR, as_of)
    if cot is None:
        return {"result": "NO_DATA", "event": "cot", "date": as_of}

    wow = cot.get("mm_net_wow")
    p1 = cot.get("mm_net_pctile_1y")
    material = (wow is not None and abs(wow) >= _COT_WOW_THRESHOLD) or (
        p1 is not None and (p1 >= _COT_PCTILE_HI or p1 <= _COT_PCTILE_LO))
    if not material:
        return {"result": "QUIET", "event": "cot", "date": as_of,
                "mm_net": cot.get("mm_net"), "wow": wow, "pctile_1y": p1}

    wow_str = f"{wow:+,}" if wow is not None else "n/a"
    p1_str = f"{p1:.0f}%ile (1y)" if p1 is not None else "n/a"
    text = "\n".join([
        "📊 *CurveLens COT Update*",
        f"_report {cot['report_date']}_ · positions as of Tuesday",
        "",
        f"*Managed money net:* {cot['mm_net']:+,} lots ({wow_str} WoW, {p1_str})",
        f"*Producer/merchant net:* {cot['prod_net']:+,}",
        "",
        "Material positioning shift — details in the next settlement brief.",
    ])
    q = queue_message("COT_UPDATE", as_of, text)
    return {"result": "UPDATE_QUEUED" if q["result"] == "QUEUED" else "ALREADY_HANDLED",
            "event": "cot", "date": as_of, "queue": q}


def main() -> None:
    parser = argparse.ArgumentParser(description="CurveLens event-calendar mini-runs")
    parser.add_argument("--event", required=True, choices=["eia", "cot"])
    parser.add_argument("--date", help="Event date YYYY-MM-DD (default: today NY)")
    args = parser.parse_args()

    as_of = args.date or _ny_today().isoformat()
    _emit(run_eia(as_of) if args.event == "eia" else run_cot(as_of))


if __name__ == "__main__":
    main()

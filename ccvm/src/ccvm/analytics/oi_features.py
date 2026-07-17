"""
Open-interest analytics from product option settlements (C2).

Per-strike OI and volume arrive free in the CME bulletin and were previously
collected and used nowhere. This module reads silver options and computes,
for the front expiries:

  - OI walls: the largest-OI strikes per side (magnet/pin candidates near expiry)
  - put/call OI ratio (positioning skew)
  - max pain: the settle that minimizes aggregate option payout at expiry
  - volume / OI ratio (new positioning vs. churn)
  - ΔOI vs the prior session's silver, per strike (where prior data exists)

Results are written as JSON to data/gold/oi/trade_date=<d>/oi.json (list
granularity fits JSON better than a fixed-schema table) and rendered as an
"Options Positioning" block in the brief.
"""
from __future__ import annotations

import logging
from typing import Optional

import pyarrow as pa

logger = logging.getLogger(__name__)

_TOP_N_WALLS = 3
_N_EXPIRIES = 2  # front expiries to analyze


def _rows(table: pa.Table) -> list[dict]:
    d = table.to_pydict()
    out = []
    for i in range(len(d.get("option_expiry", []))):
        if d.get("silver_status") and d["silver_status"][i] == "FAIL":
            continue
        out.append({
            "expiry": d["option_expiry"][i],
            "strike": d["strike"][i],
            "cp": d["call_put"][i],
            "oi": d["open_interest"][i],
            "volume": d["volume"][i],
        })
    return out


def max_pain(rows: list[dict]) -> Optional[float]:
    """Strike minimizing total intrinsic payout across all open interest."""
    strikes = sorted({r["strike"] for r in rows if r["strike"] is not None})
    if not strikes:
        return None
    best_strike, best_pay = None, None
    for s in strikes:
        pay = 0.0
        for r in rows:
            if r["oi"] is None or r["strike"] is None:
                continue
            if r["cp"] == "C" and s > r["strike"]:
                pay += (s - r["strike"]) * r["oi"]
            elif r["cp"] == "P" and s < r["strike"]:
                pay += (r["strike"] - s) * r["oi"]
        if best_pay is None or pay < best_pay:
            best_strike, best_pay = s, pay
    return best_strike


def _walls(rows: list[dict], cp: str, top_n: int = _TOP_N_WALLS) -> list[dict]:
    side = [r for r in rows if r["cp"] == cp and r["oi"]]
    side.sort(key=lambda r: -r["oi"])
    return [{"strike": r["strike"], "oi": r["oi"]} for r in side[:top_n]]


def _delta_oi(rows: list[dict], prior_rows: list[dict], top_n: int = _TOP_N_WALLS) -> list[dict]:
    prior = {(r["expiry"], r["strike"], r["cp"]): (r["oi"] or 0) for r in prior_rows}
    if not prior:
        return []
    deltas = []
    for r in rows:
        if r["oi"] is None:
            continue
        key = (r["expiry"], r["strike"], r["cp"])
        d = r["oi"] - prior.get(key, 0)
        if d != 0:
            deltas.append({"strike": r["strike"], "cp": r["cp"], "delta_oi": d})
    deltas.sort(key=lambda x: -abs(x["delta_oi"]))
    return deltas[:top_n]


def compute(
    silver_options: pa.Table,
    as_of_str: str,
    prior_silver_options: Optional[pa.Table] = None,
) -> dict:
    """OI analytics for the front _N_EXPIRIES expiries. Returns a JSON-able dict."""
    rows = _rows(silver_options)
    prior_rows = _rows(prior_silver_options) if prior_silver_options is not None else []
    expiries = sorted({r["expiry"] for r in rows})[:_N_EXPIRIES]

    out = {"trade_date": as_of_str, "expiries": []}
    for exp in expiries:
        er = [r for r in rows if r["expiry"] == exp]
        pr = [r for r in prior_rows if r["expiry"] == exp]
        call_oi = sum(r["oi"] or 0 for r in er if r["cp"] == "C")
        put_oi = sum(r["oi"] or 0 for r in er if r["cp"] == "P")
        volume = sum(r["volume"] or 0 for r in er)
        total_oi = call_oi + put_oi
        out["expiries"].append({
            "expiry": exp,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "put_call_oi_ratio": round(put_oi / call_oi, 3) if call_oi else None,
            "total_volume": volume,
            "volume_oi_ratio": round(volume / total_oi, 4) if total_oi else None,
            "max_pain": max_pain(er),
            "call_walls": _walls(er, "C"),
            "put_walls": _walls(er, "P"),
            "top_delta_oi": _delta_oi(er, pr),
        })
    return out

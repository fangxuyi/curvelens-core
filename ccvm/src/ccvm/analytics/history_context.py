"""
Historical context for headline metrics (gold layer).

Every headline number in the brief is meaningless without context: "ATM IV
21.5%" reads very differently at the 10th vs the 90th percentile of its own
history. This module reads the accumulated gold layer and, for the as-of date,
computes trailing percentiles and z-scores for:

  futures:  front settlement, curve slope ($/mo), M1-M2 spread
  options:  front-expiry ATM IV, 25Δ risk reversal, 25Δ butterfly, skew slope

plus the front settlement's position within its trailing 30-calendar-day
high/low range (feeds trigger evaluation later, C1).

Percentile is inclusive (share of history ≤ today, ×100). Z-score uses
population std. Both are None until at least _MIN_OBS observations exist —
with a young history the numbers are labeled by `lookback_days` so the reader
can judge how much to trust them. The window is capped at the trailing
`max_lookback` trade dates (default 252 ≈ one year).
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import pyarrow as pa

logger = logging.getLogger(__name__)

_MIN_OBS = 5
_RANGE_DAYS = 30  # calendar days for the high/low band

_SCHEMA = pa.schema([
    pa.field("trade_date", pa.string()),
    pa.field("lookback_days", pa.int32()),          # trade dates in window (incl. today)
    pa.field("front_settle", pa.float64()),
    pa.field("front_settle_pctile", pa.float64()),
    pa.field("front_settle_z", pa.float64()),
    pa.field("settle_30d_high", pa.float64()),
    pa.field("settle_30d_low", pa.float64()),
    pa.field("settle_range_position", pa.float64()),  # 0 = at low, 1 = at high
    pa.field("curve_slope", pa.float64()),
    pa.field("curve_slope_pctile", pa.float64()),
    pa.field("curve_slope_z", pa.float64()),
    pa.field("m1_m2_spread", pa.float64()),
    pa.field("m1_m2_pctile", pa.float64()),
    pa.field("m1_m2_z", pa.float64()),
    pa.field("atm_iv", pa.float64()),
    pa.field("atm_iv_pctile", pa.float64()),
    pa.field("atm_iv_z", pa.float64()),
    pa.field("rr25", pa.float64()),
    pa.field("rr25_pctile", pa.float64()),
    pa.field("rr25_z", pa.float64()),
    pa.field("bf25", pa.float64()),
    pa.field("bf25_pctile", pa.float64()),
    pa.field("bf25_z", pa.float64()),
    pa.field("skew_slope", pa.float64()),
    pa.field("skew_slope_pctile", pa.float64()),
    pa.field("skew_slope_z", pa.float64()),
    pa.field("source_id", pa.string()),
])


def percentile_of(values: list[float], x: float) -> Optional[float]:
    """Inclusive percentile of x within values (which should include x)."""
    if len(values) < _MIN_OBS:
        return None
    return 100.0 * sum(1 for v in values if v <= x) / len(values)


def zscore_of(values: list[float], x: float) -> Optional[float]:
    if len(values) < _MIN_OBS:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    if var <= 0:
        return 0.0
    return (x - mean) / math.sqrt(var)


def _futures_metrics(table: pa.Table) -> dict:
    """Front-row metrics from a gold futures_features table."""
    d = table.to_pydict()
    if not d.get("contract_code"):
        return {}
    return {
        "front_settle": d["settlement"][0],
        "curve_slope": d["front_back_slope"][0],
        "m1_m2_spread": d["spread_to_next"][0],
    }


def _options_metrics(table: pa.Table) -> dict:
    """Front-expiry surface metrics from a gold option_features table."""
    d = table.to_pydict()
    expiries = [e for e in d.get("option_expiry", []) if e]
    if not expiries:
        return {}
    front = min(expiries)
    i = d["option_expiry"].index(front)
    return {
        "atm_iv": d["atm_iv"][i],
        "rr25": d["risk_reversal_25d"][i],
        "bf25": d["butterfly_25d"][i],
        "skew_slope": d["skew_slope"][i],
    }


_METRICS = [
    # (key, source)  — source: "fut" or "opt"
    ("front_settle", "fut"),
    ("curve_slope", "fut"),
    ("m1_m2_spread", "fut"),
    ("atm_iv", "opt"),
    ("rr25", "opt"),
    ("bf25", "opt"),
    ("skew_slope", "opt"),
]


def compute(pq_store, as_of_str: str, max_lookback: int = 252) -> Optional[pa.Table]:
    """
    Build the one-row history-context table for as_of_str.

    Reads gold futures_features / option_features for all trade dates up to
    and including as_of_str (capped at the trailing max_lookback dates).
    Returns None if the as-of date itself has no gold futures.
    """
    fut_dates = [d for d in pq_store.list_dates("gold", "futures_features") if d <= as_of_str]
    fut_dates = fut_dates[-max_lookback:]
    if as_of_str not in fut_dates:
        logger.warning("No gold futures for %s — skipping history context", as_of_str)
        return None

    # Collect the per-date metric series
    series: dict[str, dict[str, float]] = {k: {} for k, _ in _METRICS}
    for dt in fut_dates:
        fm = _futures_metrics(pq_store.read("gold", "futures_features", dt))
        if pq_store.exists("gold", "option_features", dt):
            om = _options_metrics(pq_store.read("gold", "option_features", dt))
        else:
            om = {}
        for key, src in _METRICS:
            v = (fm if src == "fut" else om).get(key)
            if v is not None:
                series[key][dt] = v

    row: dict = {
        "trade_date": as_of_str,
        "lookback_days": len(fut_dates),
        "source_id": "gold/futures_features+option_features",
    }

    # value-field name → pctile/z-field base (only M1-M2 differs)
    _OUT_BASE = {"m1_m2_spread": "m1_m2"}
    for key, _src in _METRICS:
        s = series[key]
        today = s.get(as_of_str)
        vals = list(s.values())
        base = _OUT_BASE.get(key, key)
        row[key] = today
        row[f"{base}_pctile"] = percentile_of(vals, today) if today is not None else None
        row[f"{base}_z"] = zscore_of(vals, today) if today is not None else None

    # 30-calendar-day settle band
    from datetime import date, timedelta
    cutoff = (date.fromisoformat(as_of_str) - timedelta(days=_RANGE_DAYS)).isoformat()
    band = {dt: v for dt, v in series["front_settle"].items() if dt >= cutoff}
    if band:
        hi, lo = max(band.values()), min(band.values())
        row["settle_30d_high"], row["settle_30d_low"] = hi, lo
        today = band.get(as_of_str)
        row["settle_range_position"] = (
            (today - lo) / (hi - lo) if today is not None and hi > lo else None
        )
    else:
        row["settle_30d_high"] = row["settle_30d_low"] = row["settle_range_position"] = None

    return pa.table({f.name: [row.get(f.name)] for f in _SCHEMA}, schema=_SCHEMA)

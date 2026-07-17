"""
Machine-readable scenario triggers + daily evaluator (C1).

The scenario engine's confirmation/invalidation triggers were prose strings,
printed daily and never checked. Here each trigger is declarative —
{scenario, side, check, params} — and evaluated every run against series
built from the accumulated gold layer.

Trigger results per day:
    True   fired
    False  evaluated, did not fire
    None   not evaluable (insufficient history / missing input / manual)

Checks operate on a `series` bundle:
    series["front_settle"]  {date_str: float}   front-month settlement
    series["curve_slope"]   {date_str: float}   front_back_slope ($/mo)
    series["atm_iv"]        {date_str: float}   front-expiry ATM IV
    series["rr25"]          {date_str: float}   front-expiry 25Δ risk reversal
    series["eia_periods"]   [(period_str, crude_draw_mbbl)]  distinct, sorted asc

`kind: manual` triggers (e.g. "OPEC+ announces emergency cut") are listed in
the brief but never auto-fire — they are the agent's to judge.
"""
from __future__ import annotations

import operator
from datetime import date, timedelta
from typing import Optional

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


# ── Check functions ─────────────────────────────────────────────────────────

def _sorted_dates(s: dict) -> list[str]:
    return sorted(s.keys())


def check_threshold(series: dict, as_of: str, metric: str, op: str, value: float) -> Optional[bool]:
    """Today's metric vs a constant, e.g. rr25 >= 0.03."""
    v = series.get(metric, {}).get(as_of)
    if v is None and metric == "curve_slope_pct":
        slope = series.get("curve_slope", {}).get(as_of)
        settle = series.get("front_settle", {}).get(as_of)
        v = slope / settle if slope is not None and settle else None
    if v is None:
        return None
    return _OPS[op](v, value)


def check_breaks_band(series: dict, as_of: str, metric: str, lookback_days: int,
                      direction: str) -> Optional[bool]:
    """Today's value breaks above the max (or below the min) of the PRIOR
    lookback_days calendar days — today excluded from the band."""
    s = series.get(metric, {})
    today = s.get(as_of)
    if today is None:
        return None
    cutoff = (date.fromisoformat(as_of) - timedelta(days=lookback_days)).isoformat()
    prior = [v for d, v in s.items() if cutoff <= d < as_of]
    if len(prior) < 3:  # too little history to define a band
        return None
    return today > max(prior) if direction == "above" else today < min(prior)


def check_change_over_sessions(series: dict, as_of: str, metric: str, sessions: int,
                               op: str, value: float, absolute: bool = False,
                               relative: bool = False) -> Optional[bool]:
    """Change vs N trade sessions ago (e.g. settle − settle[-5] < 0)."""
    s = series.get(metric, {})
    dates = [d for d in _sorted_dates(s) if d <= as_of]
    if as_of not in s or len(dates) < sessions + 1:
        return None
    prior = s[dates[-(sessions + 1)]]
    change = s[as_of] - prior
    if relative:
        if not prior:
            return None
        change /= prior
    if absolute:
        change = abs(change)
    return _OPS[op](change, value)


def check_range_over_sessions(series: dict, as_of: str, metric: str, sessions: int,
                              op: str, value: float, relative: bool = False) -> Optional[bool]:
    """High-low range of the last N sessions (incl. today), e.g. range <= 6."""
    s = series.get(metric, {})
    dates = [d for d in _sorted_dates(s) if d <= as_of]
    if as_of not in s or len(dates) < sessions:
        return None
    window = [s[d] for d in dates[-sessions:]]
    observed = max(window) - min(window)
    if relative:
        if not s[as_of]:
            return None
        observed /= s[as_of]
    return _OPS[op](observed, value)


def check_consecutive_eia(series: dict, as_of: str, op: str, value: float,
                          count: int) -> Optional[bool]:
    """The last `count` distinct EIA periods all satisfy crude_draw <op> value."""
    periods = series.get("eia_periods") or []
    if len(periods) < count:
        return None
    recent = [draw for _p, draw in periods[-count:]]
    if any(d is None for d in recent):
        return None
    return all(_OPS[op](d, value) for d in recent)


_CHECKS = {
    "threshold": check_threshold,
    "breaks_band": check_breaks_band,
    "change_over_sessions": check_change_over_sessions,
    "range_over_sessions": check_range_over_sessions,
    "consecutive_eia": check_consecutive_eia,
}


# ── Trigger definitions (mirror scenario_engine prose) ──────────────────────

TRIGGERS: list[dict] = [
    # BULL confirms
    {"id": "bull_c_30d_high", "scenario": "bull", "side": "confirm", "kind": "auto",
     "description": "front-month settles above prior 30-day high",
     "check": "breaks_band",
     "params": {"metric": "front_settle", "lookback_days": 30, "direction": "above"}},
    {"id": "bull_c_call_skew", "scenario": "bull", "side": "confirm", "kind": "auto",
     "description": "25Δ call IV ≥ 3pp over puts (RR25 ≥ +3%)",
     "check": "threshold", "params": {"metric": "rr25", "op": ">=", "value": 0.03}},
    # BULL invalidations
    {"id": "bull_i_week_lower", "scenario": "bull", "side": "invalidate", "kind": "auto",
     "description": "front-month settles below the close 5 sessions ago",
     "check": "change_over_sessions",
     "params": {"metric": "front_settle", "sessions": 5, "op": "<", "value": 0.0}},
    {"id": "bull_i_contango", "scenario": "bull", "side": "invalidate", "kind": "auto",
     "description": "curve shifts to contango > 1.4% of front price per month",
     "check": "threshold", "params": {"metric": "curve_slope_pct", "op": ">", "value": 0.014}},

    # BASE confirms
    {"id": "base_c_range", "scenario": "base", "side": "confirm", "kind": "auto",
     "description": "front-month 5-session range within 8.5%",
     "check": "range_over_sessions",
     "params": {"metric": "front_settle", "sessions": 5, "op": "<=", "value": 0.085,
                "relative": True}},
    {"id": "base_c_iv_flat", "scenario": "base", "side": "confirm", "kind": "auto",
     "description": "ATM IV within ±2pp of 5 sessions ago",
     "check": "change_over_sessions",
     "params": {"metric": "atm_iv", "sessions": 5, "op": "<=", "value": 0.02, "absolute": True}},
    # BASE invalidations
    {"id": "base_i_big_move", "scenario": "base", "side": "invalidate", "kind": "auto",
     "description": "front-month moves > 7% over 5 sessions",
     "check": "change_over_sessions",
     "params": {"metric": "front_settle", "sessions": 5, "op": ">", "value": 0.07,
                "absolute": True, "relative": True}},
    {"id": "base_i_iv_move", "scenario": "base", "side": "invalidate", "kind": "auto",
     "description": "ATM IV moves > 5pp over 5 sessions",
     "check": "change_over_sessions",
     "params": {"metric": "atm_iv", "sessions": 5, "op": ">", "value": 0.05, "absolute": True}},

    # BEAR confirms
    {"id": "bear_c_30d_low", "scenario": "bear", "side": "confirm", "kind": "auto",
     "description": "front-month settles below prior 30-day low",
     "check": "breaks_band",
     "params": {"metric": "front_settle", "lookback_days": 30, "direction": "below"}},
    {"id": "bear_c_put_skew", "scenario": "bear", "side": "confirm", "kind": "auto",
     "description": "25Δ put IV ≥ 5pp over calls (RR25 ≤ −5%)",
     "check": "threshold", "params": {"metric": "rr25", "op": "<=", "value": -0.05}},
    # BEAR invalidations
    {"id": "bear_i_rally", "scenario": "bear", "side": "invalidate", "kind": "auto",
     "description": "front-month rallies > 5.5% over 5 sessions",
     "check": "change_over_sessions",
     "params": {"metric": "front_settle", "sessions": 5, "op": ">", "value": 0.055,
                "relative": True}},
    {"id": "bear_i_backwardation", "scenario": "bear", "side": "invalidate", "kind": "auto",
     "description": "curve moves into backwardation",
     "check": "threshold", "params": {"metric": "curve_slope", "op": "<", "value": 0.0}},
]


def evaluate_triggers(series: dict, as_of: str) -> list[dict]:
    """Evaluate every trigger for as_of. Returns TRIGGERS + {"fired": bool|None}."""
    results = []
    from ..fundamentals import get_provider
    from ..reference.product import get_product
    provider = get_provider(get_product().fundamentals_provider)
    product = get_product()
    definitions = [
        *TRIGGERS,
        *product.trigger_definitions,
        *(provider.trigger_definitions if provider else ()),
    ]
    for t in definitions:
        if t["kind"] == "manual" or t["check"] is None:
            fired = None
        else:
            fired = _CHECKS[t["check"]](series, as_of, **t["params"])
        results.append({**t, "fired": fired})
    return results

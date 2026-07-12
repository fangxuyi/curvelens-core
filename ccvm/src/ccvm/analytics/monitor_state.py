"""
Scenario state machine, streaks, and day-over-day diff (C1/D2/D3).

Turns the daily snapshot into a monitor:

- build_series(): metric series from the accumulated gold layer (reuses the
  history_context extractors so "front settle" means the same thing everywhere)
- update_scenario_state(): evaluates triggers (triggers.py) and advances a
  persisted per-scenario state machine in data/state/scenario_state.json
- compute_streaks(): consecutive EIA draws, days in backwardation, days the
  agreement state has been unchanged — computed fresh from gold each run
- build_day_diff(): what changed vs the prior trade date, for the brief's
  "What changed" section

State rules (v1, deliberately simple and re-derivable):
- status is recomputed every run: any fired invalidation → INVALIDATED;
  else ≥2 fired confirmations → CONFIRMED; else LIVE.
  (Invalidation wins ties — conservative.)
- `since` carries forward while the status is unchanged, resets on transition.
- a `history` map (date → status, capped) makes transitions auditable.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .history_context import _futures_metrics, _options_metrics
from .triggers import evaluate_triggers

logger = logging.getLogger(__name__)

_SCENARIOS = ("bull", "base", "bear")
_HISTORY_CAP = 90
_CONFIRM_MIN = 2


# ── Series building ─────────────────────────────────────────────────────────

def build_series(pq_store, as_of_str: str, max_lookback: int = 60) -> dict:
    """Metric series from gold, for dates ≤ as_of (trailing max_lookback)."""
    dates = [d for d in pq_store.list_dates("gold", "futures_features") if d <= as_of_str]
    dates = dates[-max_lookback:]

    series: dict = {"front_settle": {}, "curve_slope": {}, "atm_iv": {}, "rr25": {}}
    for dt in dates:
        fm = _futures_metrics(pq_store.read("gold", "futures_features", dt))
        if fm.get("front_settle") is not None:
            series["front_settle"][dt] = fm["front_settle"]
        if fm.get("curve_slope") is not None:
            series["curve_slope"][dt] = fm["curve_slope"]
        if pq_store.exists("gold", "option_features", dt):
            om = _options_metrics(pq_store.read("gold", "option_features", dt))
            if om.get("atm_iv") is not None:
                series["atm_iv"][dt] = om["atm_iv"]
            if om.get("rr25") is not None:
                series["rr25"][dt] = om["rr25"]

    # Distinct EIA periods (period → crude_draw), ascending
    periods: dict[str, float] = {}
    for dt in dates:
        if not pq_store.exists("gold", "eia_features", dt):
            continue
        ed = pq_store.read("gold", "eia_features", dt).to_pydict()
        p = (ed.get("eia_period") or [None])[0]
        draw = (ed.get("crude_draw") or [None])[0]
        if p:
            periods[p] = draw
    series["eia_periods"] = sorted(periods.items())
    return series


# ── Scenario state machine ──────────────────────────────────────────────────

def _state_path(data_dir: Path) -> Path:
    return data_dir / "state" / "scenario_state.json"


def _load_state(data_dir: Path) -> dict:
    p = _state_path(data_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, ValueError):
        logger.warning("Unreadable scenario state at %s — starting fresh", p)
        return {}


def _status_from_triggers(results: list[dict], scenario: str) -> tuple[str, dict]:
    confirms = [r for r in results if r["scenario"] == scenario and r["side"] == "confirm"]
    invalidations = [r for r in results if r["scenario"] == scenario and r["side"] == "invalidate"]
    fired_c = [r["id"] for r in confirms if r["fired"] is True]
    fired_i = [r["id"] for r in invalidations if r["fired"] is True]
    if fired_i:
        status = "invalidated"
    elif len(fired_c) >= _CONFIRM_MIN:
        status = "confirmed"
    else:
        status = "live"
    detail = {
        "confirms_fired": fired_c,
        "confirms_total_auto": sum(1 for r in confirms if r["kind"] == "auto"),
        "invalidations_fired": fired_i,
    }
    return status, detail


def update_scenario_state(pq_store, data_dir: Path, as_of_str: str) -> dict:
    """
    Evaluate all triggers for as_of, advance the persisted state machine,
    and return {"trigger_results": [...], "scenarios": {name: state}}.

    Idempotent per date: re-running the same date overwrites that date's
    entry rather than double-counting.
    """
    series = build_series(pq_store, as_of_str)
    results = evaluate_triggers(series, as_of_str)

    state = _load_state(data_dir)
    out_scenarios: dict[str, dict] = {}
    for sc in _SCENARIOS:
        status, detail = _status_from_triggers(results, sc)
        prev = state.get(sc, {})
        history = prev.get("history", {})
        # since: carry forward while unchanged (ignoring today's own entry)
        prior_dates = [d for d in sorted(history) if d < as_of_str]
        prev_status = history.get(prior_dates[-1]) if prior_dates else None
        since = prev.get("since") if status == prev_status else as_of_str
        history[as_of_str] = status
        history = dict(sorted(history.items())[-_HISTORY_CAP:])
        out_scenarios[sc] = {
            "status": status,
            "since": since or as_of_str,
            "last_evaluated": as_of_str,
            "history": history,
            **detail,
        }

    p = _state_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out_scenarios, indent=2))

    return {"trigger_results": results, "scenarios": out_scenarios}


# ── Streaks (computed fresh from gold — no persistence needed) ──────────────

def compute_streaks(pq_store, data_dir: Path, as_of_str: str, max_lookback: int = 60) -> dict:
    dates = [d for d in pq_store.list_dates("gold", "futures_features") if d <= as_of_str]
    dates = dates[-max_lookback:]

    # Days in backwardation (consecutive, ending today)
    backwardation = 0
    for dt in reversed(dates):
        fd = pq_store.read("gold", "futures_features", dt).to_pydict()
        flag = (fd.get("contango_flag") or [None])[0]
        if flag is False:
            backwardation += 1
        else:
            break

    # Consecutive EIA draw weeks (distinct periods, ending latest)
    periods: dict[str, float] = {}
    for dt in dates:
        if pq_store.exists("gold", "eia_features", dt):
            ed = pq_store.read("gold", "eia_features", dt).to_pydict()
            p = (ed.get("eia_period") or [None])[0]
            if p:
                periods[p] = (ed.get("crude_draw") or [None])[0]
    draw_weeks = 0
    for _p, draw in reversed(sorted(periods.items())):
        if draw is not None and draw > 0:
            draw_weeks += 1
        else:
            break

    # Agreement state streak (consecutive days with today's state)
    agreement_streak, agreement_state = 0, None
    for dt in reversed(dates):
        ap = data_dir / "gold" / "agreement" / f"trade_date={dt}" / "agreement.json"
        if not ap.exists():
            break
        st = json.loads(ap.read_text()).get("state")
        if agreement_state is None:
            agreement_state = st
        if st == agreement_state:
            agreement_streak += 1
        else:
            break

    return {
        "days_in_backwardation": backwardation,
        "consecutive_eia_draw_weeks": draw_weeks,
        "agreement_state": agreement_state,
        "agreement_state_streak_days": agreement_streak,
    }


# ── Day-over-day diff (D3) ──────────────────────────────────────────────────

def build_day_diff(pq_store, data_dir: Path, as_of_str: str) -> dict:
    """What changed vs the prior trade date — headline deltas + transitions."""
    dates = [d for d in pq_store.list_dates("gold", "futures_features") if d <= as_of_str]
    if as_of_str not in dates or len(dates) < 2:
        return {"status": "unavailable"}
    prior = dates[dates.index(as_of_str) - 1]

    def _metrics(dt):
        fm = _futures_metrics(pq_store.read("gold", "futures_features", dt))
        om = (_options_metrics(pq_store.read("gold", "option_features", dt))
              if pq_store.exists("gold", "option_features", dt) else {})
        return {**fm, **om}

    today, prev = _metrics(as_of_str), _metrics(prior)

    def _delta(key):
        a, b = today.get(key), prev.get(key)
        return (a - b) if a is not None and b is not None else None

    def _agreement(dt):
        ap = data_dir / "gold" / "agreement" / f"trade_date={dt}" / "agreement.json"
        return json.loads(ap.read_text()).get("state") if ap.exists() else None

    # Scenario transitions from persisted history
    transitions = []
    for sc, st in _load_state(data_dir).items():
        hist = st.get("history", {})
        if hist.get(as_of_str) and hist.get(prior) and hist[as_of_str] != hist[prior]:
            transitions.append(f"{sc.upper()}: {hist[prior]} → {hist[as_of_str]}")

    return {
        "status": "available",
        "prior_date": prior,
        "settle_change": _delta("front_settle"),
        "settle_prior": prev.get("front_settle"),
        "atm_iv_change": _delta("atm_iv"),
        "rr25_change": _delta("rr25"),
        "slope_change": _delta("curve_slope"),
        "agreement_today": _agreement(as_of_str),
        "agreement_prior": _agreement(prior),
        "scenario_transitions": transitions,
    }

"""
Self-scorecard: agreement-state hit rates (C7).

The only way the "tune after accumulating history" thresholds ever get tuned —
and the only way the agreement states earn (or honestly lose) user trust. For
every historical date, pair the day's agreement state with the front-month
forward return over the next 1/3/5 sessions, then aggregate per state:

    state                     n   fwd1d    fwd3d    fwd5d   hit(3d)
    confirmed_upside_risk     9  +0.42%   +1.10%   +1.85%    78%

Hit = forward 3-session return agrees with the state's direction (upside → up,
downside → down). Non-directional states report returns without a hit rate.
Persisted to data/state/scorecard.json; rendered in the brief once enough
samples exist (and always in the JSON for the agent).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DIRECTION = {"confirmed_upside_risk": +1, "confirmed_downside_risk": -1}
_MIN_RENDER_SAMPLES = 5  # brief shows the table once any state has this many


def compute(pq_store, data_dir: Path, as_of_str: str, max_lookback: int = 252) -> dict:
    """Build the scorecard from gold history up to as_of (inclusive)."""
    dates = [d for d in pq_store.list_dates("gold", "futures_features") if d <= as_of_str]
    dates = dates[-max_lookback:]

    settles: dict[str, float] = {}
    states: dict[str, str] = {}
    for dt in dates:
        fd = pq_store.read("gold", "futures_features", dt).to_pydict()
        if fd.get("settlement") and fd["settlement"][0] is not None:
            settles[dt] = fd["settlement"][0]
        ap = data_dir / "gold" / "agreement" / f"trade_date={dt}" / "agreement.json"
        if ap.exists():
            st = json.loads(ap.read_text()).get("state")
            if st:
                states[dt] = st

    ordered = [d for d in dates if d in settles]
    idx = {d: i for i, d in enumerate(ordered)}

    def _fwd_return(dt: str, sessions: int):
        i = idx.get(dt)
        if i is None or i + sessions >= len(ordered):
            return None
        a, b = settles[ordered[i]], settles[ordered[i + sessions]]
        return (b - a) / a if a else None

    per_state: dict[str, dict] = {}
    for dt, st in states.items():
        g = per_state.setdefault(st, {"n": 0, "fwd": {1: [], 3: [], 5: []}, "hits3": []})
        g["n"] += 1
        for k in (1, 3, 5):
            r = _fwd_return(dt, k)
            if r is not None:
                g["fwd"][k].append(r)
        direction = _DIRECTION.get(st)
        r3 = _fwd_return(dt, 3)
        if direction is not None and r3 is not None:
            g["hits3"].append(1 if r3 * direction > 0 else 0)

    rows = []
    for st, g in sorted(per_state.items(), key=lambda kv: -kv[1]["n"]):
        row = {"state": st, "n": g["n"]}
        for k in (1, 3, 5):
            vals = g["fwd"][k]
            row[f"avg_fwd_{k}d"] = round(sum(vals) / len(vals), 5) if vals else None
            row[f"n_fwd_{k}d"] = len(vals)
        row["hit_rate_3d"] = (round(sum(g["hits3"]) / len(g["hits3"]), 3)
                              if g["hits3"] else None)
        row["n_hits_3d"] = len(g["hits3"])
        rows.append(row)

    out = {
        "as_of": as_of_str,
        "dates_covered": len(ordered),
        "render_ready": any(r["n_fwd_3d"] >= _MIN_RENDER_SAMPLES for r in rows),
        "states": rows,
    }
    p = data_dir / "state" / "scorecard.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))
    return out

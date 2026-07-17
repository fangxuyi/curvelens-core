"""
Risk-neutral density + expected move (C3, Breeden–Litzenberger 1978).

The full strike surface is collected daily; differentiating the call-price
curve twice gives the market-implied ("risk-neutral") distribution of the
settle at expiry:

    f(K) = e^{rT} · ∂²C/∂K²

From it, the brief can finally make the statements the project's thesis
promised — "interpret the surface as market-implied expectations":

    P(F_T > $75) = 28% · expected move ±$4.10 by 2026-08-17
    RN mean/σ/skew of the settle distribution

Construction notes:
- The call curve is built OTM-only: real call settles for K ≥ F, and
  put-call parity C(K) = P(K) + df·(F−K) for K < F — sidestepping CME's
  ITM-settlement inconsistency (see the active product's conventions.md).
- Second derivative via central differences on the (uneven) strike grid;
  negative densities are clipped and the mass renormalized. `raw_mass`
  (pre-normalization integral) is a published diagnostic — far from 1.0
  means a noisy/incomplete strike grid, treat moments with suspicion.
- Expected move is straddle-implied: E|F_T − F| = e^{rT}·(C(F)+P(F)),
  interpolated at the forward.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import pyarrow as pa

logger = logging.getLogger(__name__)

_MIN_STRIKES = 15
_MIN_PRICE = 0.015  # ignore strikes at the 1-cent floor (pure noise for f'')


def _call_curve(rows: list[dict], fwd: float, df: float) -> list[tuple[float, float]]:
    """(strike, call_price) OTM-constructed: calls above F, parity-puts below."""
    calls = {r["strike"]: r["settlement"] for r in rows
             if r["cp"] == "C" and r["strike"] >= fwd and r["settlement"] >= _MIN_PRICE}
    puts = {r["strike"]: r["settlement"] for r in rows
            if r["cp"] == "P" and r["strike"] < fwd and r["settlement"] >= _MIN_PRICE}
    curve = dict(calls)
    for k, p in puts.items():
        curve[k] = p + df * (fwd - k)
    return sorted(curve.items())


def _density(curve: list[tuple[float, float]], rT_factor: float) -> list[tuple[float, float]]:
    """f(K_i) = rT_factor · d²C/dK² by central differences on an uneven grid."""
    out = []
    for i in range(1, len(curve) - 1):
        k0, c0 = curve[i - 1]
        k1, c1 = curve[i]
        k2, c2 = curve[i + 1]
        h1, h2 = k1 - k0, k2 - k1
        if h1 <= 0 or h2 <= 0:
            continue
        second = 2.0 * ((c2 - c1) / h2 - (c1 - c0) / h1) / (h1 + h2)
        out.append((k1, max(0.0, rT_factor * second)))
    return out


def _integrate(density: list[tuple[float, float]]) -> float:
    """Trapezoid integral of the density."""
    total = 0.0
    for i in range(1, len(density)):
        k0, f0 = density[i - 1]
        k1, f1 = density[i]
        total += 0.5 * (f0 + f1) * (k1 - k0)
    return total


def _moments(density: list[tuple[float, float]]) -> dict:
    """Mean, stdev, skewness from a normalized discrete density (trapezoid)."""
    def _int(fn):
        total = 0.0
        for i in range(1, len(density)):
            k0, f0 = density[i - 1]
            k1, f1 = density[i]
            total += 0.5 * (fn(k0) * f0 + fn(k1) * f1) * (k1 - k0)
        return total

    mean = _int(lambda k: k)
    var = _int(lambda k: (k - mean) ** 2)
    std = math.sqrt(var) if var > 0 else 0.0
    skew = _int(lambda k: (k - mean) ** 3) / std ** 3 if std > 0 else 0.0
    return {"mean": mean, "std": std, "skew": skew}


def _prob_above(density: list[tuple[float, float]], x: float) -> float:
    """P(F_T > x) by trapezoid over the density above x (with edge interpolation)."""
    total = 0.0
    for i in range(1, len(density)):
        k0, f0 = density[i - 1]
        k1, f1 = density[i]
        if k1 <= x:
            continue
        if k0 >= x:
            total += 0.5 * (f0 + f1) * (k1 - k0)
        else:  # segment straddles x — take the part above
            t = (x - k0) / (k1 - k0)
            fx = f0 + t * (f1 - f0)
            total += 0.5 * (fx + f1) * (k1 - x)
    return total


def _interp_price(rows: list[dict], cp: str, x: float) -> Optional[float]:
    """Linear interpolation of settle at strike x for one side."""
    pts = sorted((r["strike"], r["settlement"]) for r in rows
                 if r["cp"] == cp and r["settlement"] is not None)
    for i in range(1, len(pts)):
        k0, p0 = pts[i - 1]
        k1, p1 = pts[i]
        if k0 <= x <= k1:
            t = (x - k0) / (k1 - k0) if k1 > k0 else 0.0
            return p0 + t * (p1 - p0)
    return None


def compute_expiry(rows: list[dict], fwd: float, tte: float, rate: float) -> Optional[dict]:
    """RND summary for one expiry from its option rows."""
    df = math.exp(-rate * tte)
    curve = _call_curve(rows, fwd, df)
    if len(curve) < _MIN_STRIKES:
        return None

    density = _density(curve, 1.0 / df)  # e^{rT} = 1/df
    raw_mass = _integrate(density)
    if raw_mass <= 0.5:  # grid too broken to normalize honestly
        return None
    density = [(k, f / raw_mass) for k, f in density]

    m = _moments(density)

    # Straddle-implied expected move at the forward
    c_at_f = _interp_price(rows, "C", fwd)
    p_at_f = _interp_price(rows, "P", fwd)
    expected_move = ((c_at_f + p_at_f) / df) if c_at_f is not None and p_at_f is not None else None

    # Probability ladder: round strikes near the forward
    ladder = {}
    for x in (round(fwd / 5) * 5 + d for d in (-10, -5, 0, 5, 10)):
        if density[0][0] < x < density[-1][0]:
            ladder[f"p_above_{x:.0f}"] = round(_prob_above(density, x), 4)

    return {
        "forward": fwd,
        "n_strikes": len(curve),
        "raw_mass": round(raw_mass, 4),
        "rn_mean": round(m["mean"], 3),
        "rn_std": round(m["std"], 3),
        "rn_skew": round(m["skew"], 3),
        "expected_move_straddle": round(expected_move, 3) if expected_move is not None else None,
        "prob_ladder": ladder,
    }


def compute(gold_options: pa.Table, as_of_str: str, rate: float | None = None,
            n_expiries: int = 2) -> dict:
    """RND summaries for the front n expiries from a gold option_features table."""
    if rate is None:
        from ..reference.product import get_product
        rate = get_product().risk_free_rate
    d = gold_options.to_pydict()
    by_expiry: dict[str, dict] = {}
    for i in range(len(d["option_expiry"])):
        exp = d["option_expiry"][i]
        e = by_expiry.setdefault(exp, {"rows": [], "fwd": None, "tte": None})
        if d["settlement"][i] is not None and d["strike"][i] is not None:
            e["rows"].append({"strike": d["strike"][i], "cp": d["call_put"][i],
                              "settlement": d["settlement"][i]})
        if e["fwd"] is None and d["forward_price"][i] is not None:
            e["fwd"] = d["forward_price"][i]
            e["tte"] = d["time_to_expiry_years"][i]

    out = {"trade_date": as_of_str, "expiries": []}
    for exp in sorted(by_expiry)[:n_expiries]:
        e = by_expiry[exp]
        if e["fwd"] is None or e["tte"] is None or e["tte"] <= 0:
            continue
        r = compute_expiry(e["rows"], e["fwd"], e["tte"], rate)
        if r is not None:
            out["expiries"].append({"expiry": exp, **r})
    return out

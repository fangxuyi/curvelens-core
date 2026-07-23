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
- Raw finite-difference diagnostics are retained, but probabilities are not
  computed by differentiating those noisy values.  Instead, a non-negative
  terminal-state distribution is calibrated to all OTM-derived call prices.
  Unit mass and the forward moment are explicit constraints, which makes the
  fitted call curve decreasing and convex by construction.  A small density-
  smoothness penalty makes the inverse problem stable under exchange rounding.
  The fit is accepted only when quote residuals remain within the product's
  tick-aware limit and neither tail boundary holds material probability.
- Expected move is straddle-implied: E|F_T − F| = e^{rT}·(C(F)+P(F)),
  interpolated at the forward.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Optional

import pyarrow as pa
import numpy as np
from scipy.optimize import lsq_linear

from .black76 import black76_price

logger = logging.getLogger(__name__)

_MIN_STRIKES = 15
_MIN_PRICE = 0.015  # ignore strikes at the 1-cent floor (pure noise for f'')
_MAX_NEGATIVE_MASS = 0.05
_MAX_SIGNED_MASS_ERROR = 0.10
_MAX_TAIL_BOUNDARY_MASS = 0.025
_CONSTRAINT_WEIGHT = 100_000.0
_SMOOTHNESS_WEIGHT = 2.0


def _call_curve(rows: list[dict], fwd: float, df: float) -> list[tuple[float, float]]:
    """(strike, call_price) OTM-constructed: calls above F, parity-puts below."""
    calls = {r["strike"]: r.get("curve_settlement", r["settlement"]) for r in rows
             if r["cp"] == "C" and r["strike"] >= fwd
             and r.get("curve_settlement", r["settlement"]) >= _MIN_PRICE}
    puts = {r["strike"]: r.get("curve_settlement", r["settlement"]) for r in rows
            if r["cp"] == "P" and r["strike"] < fwd
            and r.get("curve_settlement", r["settlement"]) >= _MIN_PRICE}
    curve = dict(calls)
    for k, p in puts.items():
        curve[k] = p + df * (fwd - k)
    return sorted(curve.items())


def _density(curve: list[tuple[float, float]], rT_factor: float) -> list[tuple[float, float]]:
    """Signed f(K_i) = rT_factor · d²C/dK² on an uneven grid."""
    out = []
    for i in range(1, len(curve) - 1):
        k0, c0 = curve[i - 1]
        k1, c1 = curve[i]
        k2, c2 = curve[i + 1]
        h1, h2 = k1 - k0, k2 - k1
        if h1 <= 0 or h2 <= 0:
            continue
        second = 2.0 * ((c2 - c1) / h2 - (c1 - c0) / h1) / (h1 + h2)
        out.append((k1, rT_factor * second))
    return out


def _cell_widths(support: np.ndarray) -> np.ndarray:
    """Integration widths for density values located on an uneven grid."""
    widths = np.empty_like(support)
    widths[0] = support[1] - support[0]
    widths[-1] = support[-1] - support[-2]
    widths[1:-1] = 0.5 * (support[2:] - support[:-2])
    return widths


def _fit_state_prices(
    curve: list[tuple[float, float]], fwd: float, df: float, fit_unit: float,
) -> dict:
    """Fit a smooth non-negative terminal distribution to call prices.

    The unknowns are probability masses at the observed strikes.  Non-negative
    masses make every repriced call curve decreasing and convex.  Two strongly
    weighted rows impose total probability one and E[S_T] = forward.  The
    residual checks below remain authoritative, so a failed optimizer or a fit
    that needs material price changes is never reported as a probability.
    """
    quote_strikes = np.asarray([k for k, _ in curve], dtype=float)
    prices = np.asarray([c for _, c in curve], dtype=float)
    median_step = float(np.median(np.diff(quote_strikes)))
    span = float(quote_strikes[-1] - quote_strikes[0])
    extension = max(12.0 * median_step, 0.5 * span)
    lower = max(0.0, quote_strikes[0] - extension)
    upper = quote_strikes[-1] + extension
    left_tail = np.linspace(lower, quote_strikes[0], 13, endpoint=False)
    right_tail = np.linspace(quote_strikes[-1], upper, 14)[1:]
    strikes = np.unique(np.concatenate([left_tail, quote_strikes, right_tail]))
    widths = _cell_widths(strikes)
    payoff = df * np.maximum(strikes[None, :] - quote_strikes[:, None], 0.0)

    # Penalize changes in probability density, not raw point mass, so an uneven
    # strike grid does not receive artificial spikes merely from wider cells.
    n = len(strikes)
    density_smooth = np.zeros((max(0, n - 2), n))
    for i in range(n - 2):
        left = strikes[i + 1] - strikes[i]
        right = strikes[i + 2] - strikes[i + 1]
        density_smooth[i, i] = 1.0 / (widths[i] * left)
        density_smooth[i, i + 1] = -(1.0 / left + 1.0 / right) / widths[i + 1]
        density_smooth[i, i + 2] = 1.0 / (widths[i + 2] * right)

    matrix = np.vstack([
        payoff / fit_unit,
        _CONSTRAINT_WEIGHT * np.ones((1, n)),
        _CONSTRAINT_WEIGHT * (strikes / fwd)[None, :],
        _SMOOTHNESS_WEIGHT * density_smooth,
    ])
    target = np.concatenate([
        prices / fit_unit,
        np.array([_CONSTRAINT_WEIGHT, _CONSTRAINT_WEIGHT]),
        np.zeros(len(density_smooth)),
    ])
    solved = lsq_linear(
        matrix, target, bounds=(0.0, np.inf), method="trf",
        tol=1e-10, lsmr_tol=1e-10, max_iter=1_000,
    )
    probability = np.maximum(solved.x, 0.0)
    mass = float(probability.sum())
    mean = float(probability @ strikes / mass) if mass > 0 else math.nan
    fitted_prices = payoff @ probability
    adjustments = np.abs(fitted_prices - prices)
    return {
        "success": bool(solved.success),
        "message": solved.message,
        "strikes": strikes,
        "probability": probability,
        "density": probability / widths,
        "mass": mass,
        "mean": mean,
        "fitted_prices": fitted_prices,
        "max_adjustment": float(adjustments.max(initial=0.0)),
        "mean_adjustment": float(adjustments.mean()) if len(adjustments) else 0.0,
        "tail_boundary_mass": float(probability[0] + probability[-1]),
    }


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


def _state_prob_above(strikes: np.ndarray, probability: np.ndarray, x: float) -> float:
    """P(S_T > x), linearly splitting a mass when x crosses its grid cell."""
    if x < strikes[0]:
        return float(probability.sum())
    if x >= strikes[-1]:
        return 0.0
    i = int(np.searchsorted(strikes, x, side="right"))
    total = float(probability[i:].sum())
    left, right = strikes[i - 1], strikes[i]
    if right > left:
        total += float(probability[i - 1]) * (right - x) / (right - left)
    return total


def _quantile(strikes: np.ndarray, probability: np.ndarray, q: float) -> float:
    cumulative = np.cumsum(probability) / probability.sum()
    return float(np.interp(q, cumulative, strikes))


def _display_decimals(strikes: np.ndarray) -> int:
    median_step = float(np.median(np.diff(strikes)))
    if median_step >= 1.0:
        return 0
    if median_step >= 0.1:
        return 1
    return 2


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


def compute_expiry(
    rows: list[dict], fwd: float, tte: float, rate: float,
    *, price_tick: float | None = None, max_projection_ticks: float = 2.0,
) -> Optional[dict]:
    """RND summary for one expiry from its option rows."""
    df = math.exp(-rate * tte)
    key_counts = Counter((r["cp"], r["strike"]) for r in rows)
    duplicate_keys = sum(1 for count in key_counts.values() if count > 1)
    duplicate_rows = sum(count - 1 for count in key_counts.values() if count > 1)
    curve = _call_curve(rows, fwd, df)
    if len(curve) < _MIN_STRIKES:
        return None

    raw_density = _density(curve, 1.0 / df)  # e^{rT} = 1/df
    signed_mass = _integrate(raw_density)
    positive_mass = _integrate([(k, max(0.0, f)) for k, f in raw_density])
    negative_mass = _integrate([(k, max(0.0, -f)) for k, f in raw_density])
    convexity_violations = sum(1 for _, f in raw_density if f < -1e-10)
    monotonicity_violations = sum(
        1 for (_, c0), (_, c1) in zip(curve, curve[1:]) if c1 > c0 + 1e-10
    )

    # Use the configured premium tick as the economically meaningful error
    # scale.  Direct callers without a product profile still get a conservative
    # synthetic scale suitable for diagnostics and tests.
    fit_unit = price_tick if price_tick is not None and price_tick > 0 else max(1e-4, fwd * 1e-5)
    fitted = _fit_state_prices(curve, fwd, df, fit_unit)
    max_projection_adjustment = fitted["max_adjustment"]
    mean_projection_adjustment = fitted["mean_adjustment"]
    max_projection_adjustment_ticks = (
        max_projection_adjustment / price_tick
        if price_tick is not None and price_tick > 0 else None
    )
    fit_error_units = max_projection_adjustment / fit_unit
    fit_is_bounded = fit_error_units <= max_projection_ticks
    projected_mass = fitted["mass"]
    fitted_forward_error = fitted["mean"] - fwd

    reasons = []
    warnings = []
    if duplicate_keys:
        reasons.append(f"{duplicate_keys} duplicate call/put-strike keys ({duplicate_rows} extra rows)")
    if monotonicity_violations:
        if fit_is_bounded:
            warnings.append(
                f"the constrained fit absorbed {monotonicity_violations} raw call-price "
                f"monotonicity violations"
            )
        else:
            reasons.append(f"{monotonicity_violations} call-price monotonicity violations")
    if abs(signed_mass - 1.0) > _MAX_SIGNED_MASS_ERROR:
        warnings.append(
            f"raw finite-difference mass {signed_mass:.4f} is not near 1; "
            "probabilities use the constrained state-price fit"
        )
    if negative_mass > _MAX_NEGATIVE_MASS:
        if fit_is_bounded:
            warnings.append(
                f"raw finite differences produced negative mass {negative_mass:.4f}; "
                "the non-negative state-price fit is used instead"
            )
        else:
            reasons.append(
                f"negative density mass {negative_mass:.4f} exceeds {_MAX_NEGATIVE_MASS:.2f}"
            )
    if not fitted["success"]:
        reasons.append(f"state-price calibration failed: {fitted['message']}")
    if not fit_is_bounded:
        unit_label = "premium ticks" if price_tick is not None else "fit units"
        reasons.append(
            f"constrained fit requires {fit_error_units:.2f} {unit_label}; "
            f"limit is {max_projection_ticks:.2f}"
        )
    if abs(projected_mass - 1.0) > 1e-4:
        reasons.append(f"fitted probability mass {projected_mass:.6f} is not 1")
    if abs(fitted_forward_error) > max(fwd * 1e-4, fit_unit):
        reasons.append(
            f"fitted mean {fitted['mean']:.4f} does not reproduce forward {fwd:.4f}"
        )
    if fitted["tail_boundary_mass"] > _MAX_TAIL_BOUNDARY_MASS:
        reasons.append(
            f"tail boundary mass {fitted['tail_boundary_mass']:.4f} exceeds "
            f"{_MAX_TAIL_BOUNDARY_MASS:.3f}; strike coverage is insufficient"
        )

    diagnostics = {
        "raw_mass": round(signed_mass, 4),  # compatibility: now genuinely pre-clipping
        "signed_mass": round(signed_mass, 4),
        "positive_mass": round(positive_mass, 4),
        "negative_mass": round(negative_mass, 4),
        "convexity_violations": convexity_violations,
        "monotonicity_violations": monotonicity_violations,
        "duplicate_keys": duplicate_keys,
        "duplicate_rows": duplicate_rows,
        "projection_applied": max_projection_adjustment > 1e-10,
        "projection_max_adjustment": round(max_projection_adjustment, 6),
        "projection_mean_adjustment": round(mean_projection_adjustment, 6),
        "projection_max_adjustment_ticks": (
            round(max_projection_adjustment_ticks, 4)
            if max_projection_adjustment_ticks is not None else None
        ),
        "projection_limit_ticks": max_projection_ticks,
        "fit_max_residual": round(max_projection_adjustment, 6),
        "fit_mean_residual": round(mean_projection_adjustment, 6),
        "fit_max_residual_ticks": (
            round(max_projection_adjustment_ticks, 4)
            if max_projection_adjustment_ticks is not None else None
        ),
        "fit_residual_limit_ticks": max_projection_ticks,
        "projected_mass": round(projected_mass, 4),
        "fitted_forward": round(fitted["mean"], 4),
        "fitted_forward_error": round(fitted_forward_error, 6),
        "tail_boundary_mass": round(fitted["tail_boundary_mass"], 6),
        "calibration_method": "nonnegative_state_prices_with_mass_and_forward_constraints",
        "validation_warnings": warnings,
    }

    # Straddle remains a directly observed statistic even when the RND is bad.
    c_at_f = _interp_price(rows, "C", fwd)
    p_at_f = _interp_price(rows, "P", fwd)
    expected_move = ((c_at_f + p_at_f) / df) if c_at_f is not None and p_at_f is not None else None
    if reasons:
        return {
            "status": "invalid_surface", "forward": fwd,
            "n_strikes": len(curve), **diagnostics,
            "rn_mean": None, "rn_std": None, "rn_skew": None,
            "expected_move_straddle": round(expected_move, 3) if expected_move is not None else None,
            "prob_ladder": {}, "validation_errors": reasons,
        }

    if projected_mass <= 0.5:
        return None
    strikes = fitted["strikes"]
    probability = fitted["probability"] / projected_mass
    mean = float(probability @ strikes)
    variance = float(probability @ ((strikes - mean) ** 2))
    std = math.sqrt(max(0.0, variance))
    skew = (
        float(probability @ ((strikes - mean) ** 3)) / std ** 3
        if std > 0 else 0.0
    )

    # Product- and maturity-scaled thresholds remain informative for both WTI
    # and Gold, unlike the former fixed +/-$10 ladder.
    decimals = _display_decimals(strikes)
    thresholds = [fwd + multiple * std for multiple in (-2, -1, 0, 1, 2)]
    thresholds = [round(x, decimals) for x in thresholds if strikes[0] < x < strikes[-1]]
    ladder = {
        f"p_above_{x:.{decimals}f}": round(_state_prob_above(strikes, probability, x), 4)
        for x in dict.fromkeys(thresholds)
    }
    quantiles = {
        f"p{int(q * 100):02d}": round(_quantile(strikes, probability, q), decimals)
        for q in (0.05, 0.25, 0.50, 0.75, 0.95)
    }
    bucket_edges = [fwd - std, fwd, fwd + std]
    cdf = [1.0 - _state_prob_above(strikes, probability, edge) for edge in bucket_edges]
    probability_buckets = [
        {"label": "below_minus_1sd", "upper": round(bucket_edges[0], decimals),
         "probability": round(cdf[0], 4)},
        {"label": "minus_1sd_to_forward", "lower": round(bucket_edges[0], decimals),
         "upper": round(fwd, decimals), "probability": round(cdf[1] - cdf[0], 4)},
        {"label": "forward_to_plus_1sd", "lower": round(fwd, decimals),
         "upper": round(bucket_edges[2], decimals), "probability": round(cdf[2] - cdf[1], 4)},
        {"label": "above_plus_1sd", "lower": round(bucket_edges[2], decimals),
         "probability": round(1.0 - cdf[2], 4)},
    ]
    density_points = [
        {"strike": round(float(k), decimals + 1),
         "density": round(float(d), 8),
         "probability_mass": round(float(p), 8)}
        for k, d, p in zip(strikes, fitted["density"] / projected_mass, probability)
    ]

    return {
        "status": "available",
        "forward": fwd,
        "n_strikes": len(curve),
        **diagnostics,
        "rn_mean": round(mean, 3),
        "rn_std": round(std, 3),
        "rn_skew": round(skew, 3),
        "expected_move_straddle": round(expected_move, 3) if expected_move is not None else None,
        "prob_ladder": ladder,
        "quantiles": quantiles,
        "probability_buckets": probability_buckets,
        "density_points": density_points,
    }


def compute(gold_options: pa.Table, as_of_str: str, rate: float | None = None,
            n_expiries: int = 2) -> dict:
    """RND summaries for the front n expiries from a gold option_features table."""
    from ..reference.product import get_product
    product = get_product()
    if rate is None:
        rate = product.risk_free_rate
    d = gold_options.to_pydict()
    by_expiry: dict[str, dict] = {}
    for i in range(len(d["option_expiry"])):
        exp = d["option_expiry"][i]
        e = by_expiry.setdefault(exp, {"rows": [], "fwd": None, "tte": None})
        if d["settlement"][i] is not None and d["strike"][i] is not None:
            row = {"strike": d["strike"][i], "cp": d["call_put"][i],
                   "settlement": d["settlement"][i]}
            baw_iv = (d.get("baw_iv") or [None] * len(d["option_expiry"]))[i]
            fwd_i = d["forward_price"][i]
            tte_i = d["time_to_expiry_years"][i]
            if product.exercise_style.lower() == "american" and baw_iv is not None \
                    and baw_iv > 0 and fwd_i is not None and tte_i is not None and tte_i > 0:
                row["curve_settlement"] = black76_price(
                    fwd_i, d["strike"][i], tte_i, rate, baw_iv, d["call_put"][i]
                )
                e["exercise_adjusted_rows"] = e.get("exercise_adjusted_rows", 0) + 1
            e["rows"].append(row)
        if e["fwd"] is None and d["forward_price"][i] is not None:
            e["fwd"] = d["forward_price"][i]
            e["tte"] = d["time_to_expiry_years"][i]

    out = {"trade_date": as_of_str, "expiries": []}
    for exp in sorted(by_expiry)[:n_expiries]:
        e = by_expiry[exp]
        if e["fwd"] is None or e["tte"] is None or e["tte"] <= 0:
            continue
        r = compute_expiry(
            e["rows"], e["fwd"], e["tte"], rate,
            price_tick=product.option_premium_tick_size,
            max_projection_ticks=product.rnd_max_fit_residual_ticks,
        )
        if r is not None:
            adjusted_rows = e.get("exercise_adjusted_rows", 0)
            r["method"] = (
                "otm_european_equivalent_constrained_state_price_calibration"
                if adjusted_rows else "otm_settlement_constrained_state_price_calibration"
            )
            r["exercise_style"] = product.exercise_style
            r["exercise_adjustment"] = (
                "baw_iv_to_black76" if adjusted_rows else (
                    "unavailable" if product.exercise_style.lower() == "american"
                    else "not_required"
                )
            )
            r["exercise_adjusted_rows"] = adjusted_rows
            out["expiries"].append({"expiry": exp, **r})
    return out

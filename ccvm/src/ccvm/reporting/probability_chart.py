"""Display-only transformations for validated risk-neutral probabilities."""
from __future__ import annotations

import math
from typing import Any


def fitted_probability_above_curve(
    density_points: list[dict[str, Any]],
    *,
    lower: float | None = None,
    upper: float | None = None,
) -> list[dict[str, float]]:
    """Return the direct reverse cumulative mass on the fitted strike grid.

    Each point is ``P(S_T >= strike)`` on an actual fitted state-price node.
    Bounds only filter the displayed nodes after the full cumulative sum; they
    do not create or interpolate additional strikes.
    """
    if lower is not None and not math.isfinite(lower):
        return []
    if upper is not None and not math.isfinite(upper):
        return []
    if lower is not None and upper is not None and lower >= upper:
        return []

    by_strike: dict[float, float] = {}
    for point in density_points:
        try:
            strike = float(point["strike"])
            mass = float(point["probability_mass"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(strike) and math.isfinite(mass) and mass >= 0:
            by_strike[strike] = by_strike.get(strike, 0.0) + mass

    states = sorted(by_strike.items())
    total_mass = sum(mass for _, mass in states)
    if not states or total_mass <= 0:
        return []

    remaining = 1.0
    curve: list[dict[str, float]] = []
    for strike, mass in states:
        if ((lower is None or strike >= lower)
                and (upper is None or strike <= upper)):
            curve.append({
                "strike": strike,
                "probability_at_or_above": min(1.0, max(0.0, remaining)),
            })
        remaining -= mass / total_mass
    return curve

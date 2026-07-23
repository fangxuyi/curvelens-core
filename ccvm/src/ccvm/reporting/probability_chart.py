"""Display-only transformations for validated risk-neutral probabilities."""
from __future__ import annotations

import math
from typing import Any


def dense_probability_above_curve(
    density_points: list[dict[str, Any]],
    lower: float,
    upper: float,
    *,
    point_count: int = 121,
) -> list[dict[str, float]]:
    """Interpolate a dense survival curve from fitted state-price masses.

    This follows the same within-cell convention used by the RND engine: mass
    at the left strike is allocated linearly across the interval to the next
    strike. It is a display interpolation, not additional market information.
    """
    if point_count < 2 or not math.isfinite(lower) or not math.isfinite(upper):
        return []
    if lower >= upper:
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
    if len(states) < 2 or total_mass <= 0:
        return []

    strikes = [strike for strike, _ in states]
    masses = [mass / total_mass for _, mass in states]
    suffix_mass = [0.0] * (len(masses) + 1)
    for index in range(len(masses) - 1, -1, -1):
        suffix_mass[index] = suffix_mass[index + 1] + masses[index]

    def probability_above(threshold: float) -> float:
        if threshold < strikes[0]:
            return 1.0
        if threshold >= strikes[-1]:
            return 0.0

        right_index = next(
            index for index, strike in enumerate(strikes) if strike > threshold
        )
        left_index = right_index - 1
        left, right = strikes[left_index], strikes[right_index]
        partial_left_mass = masses[left_index] * (
            (right - threshold) / (right - left)
        )
        return min(1.0, max(0.0, suffix_mass[right_index] + partial_left_mass))

    step = (upper - lower) / (point_count - 1)
    return [
        {
            "strike": lower + index * step,
            "probability_above": probability_above(lower + index * step),
        }
        for index in range(point_count)
    ]

from __future__ import annotations

import pytest

from ccvm.reporting.probability_chart import fitted_probability_above_curve


def test_probability_curve_is_direct_reverse_cumulative_mass():
    curve = fitted_probability_above_curve(
        [
            {"strike": 90.0, "probability_mass": 0.25},
            {"strike": 100.0, "probability_mass": 0.50},
            {"strike": 110.0, "probability_mass": 0.25},
        ],
    )

    assert [point["strike"] for point in curve] == [90, 100, 110]
    assert [point["probability_at_or_above"] for point in curve] == pytest.approx(
        [1.0, 0.75, 0.25]
    )
    assert all(
        curve[index]["probability_at_or_above"]
        >= curve[index + 1]["probability_at_or_above"]
        for index in range(len(curve) - 1)
    )


def test_probability_curve_filters_after_cumulative_sum_and_normalizes_mass():
    points = [
        {"strike": 90, "probability_mass": 2},
        {"strike": 100, "probability_mass": 2},
        {"strike": 110, "probability_mass": 4},
    ]
    curve = fitted_probability_above_curve(points, lower=95, upper=105)
    assert curve == [{"strike": 100.0, "probability_at_or_above": 0.75}]
    assert fitted_probability_above_curve(points, lower=100, upper=90) == []

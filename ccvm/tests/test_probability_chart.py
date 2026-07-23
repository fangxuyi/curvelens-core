from __future__ import annotations

import pytest

from ccvm.reporting.probability_chart import dense_probability_above_curve


def test_dense_probability_curve_uses_fitted_state_masses():
    curve = dense_probability_above_curve(
        [
            {"strike": 90.0, "probability_mass": 0.25},
            {"strike": 100.0, "probability_mass": 0.50},
            {"strike": 110.0, "probability_mass": 0.25},
        ],
        90.0,
        110.0,
        point_count=5,
    )

    assert [point["strike"] for point in curve] == [90, 95, 100, 105, 110]
    assert [point["probability_above"] for point in curve] == pytest.approx(
        [1.0, 0.875, 0.75, 0.50, 0.0]
    )
    assert all(
        curve[index]["probability_above"]
        >= curve[index + 1]["probability_above"]
        for index in range(len(curve) - 1)
    )


def test_dense_probability_curve_normalizes_rounded_mass_and_rejects_bad_range():
    points = [
        {"strike": 90, "probability_mass": 2},
        {"strike": 100, "probability_mass": 2},
    ]
    curve = dense_probability_above_curve(points, 80, 90, point_count=3)
    assert curve[0]["probability_above"] == pytest.approx(1.0)
    assert dense_probability_above_curve(points, 100, 90) == []

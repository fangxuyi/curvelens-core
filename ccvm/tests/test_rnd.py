"""Tests for the risk-neutral density module (C3).

The key validation: densities extracted from synthetic Black-76 prices must
recover the known lognormal — P(F_T > K) ≈ N(d2) and RN mean ≈ forward.
"""
from __future__ import annotations

import math

import pyarrow as pa
import pytest

from ccvm.analytics.black76 import black76_price
from ccvm.analytics.rnd import _prob_above, compute, compute_expiry


def _synthetic_rows(F=70.0, T=0.15, r=0.05, vol=0.25, lo=40, hi=110, step=0.5):
    rows = []
    k = lo
    while k <= hi:
        rows.append({"strike": k, "cp": "C",
                     "settlement": black76_price(F, k, T, r, vol, "C")})
        rows.append({"strike": k, "cp": "P",
                     "settlement": black76_price(F, k, T, r, vol, "P")})
        k += step
    return rows


def _n_d2(F, K, T, vol):
    d2 = (math.log(F / K) - 0.5 * vol * vol * T) / (vol * math.sqrt(T))
    return 0.5 * (1 + math.erf(d2 / math.sqrt(2)))


class TestLognormalRecovery:
    F, T, R, VOL = 70.0, 0.15, 0.05, 0.25

    def _result(self):
        return compute_expiry(_synthetic_rows(self.F, self.T, self.R, self.VOL),
                              self.F, self.T, self.R)

    def test_mass_near_one(self):
        r = self._result()
        assert r is not None
        assert r["raw_mass"] == pytest.approx(1.0, abs=0.05)

    def test_rn_mean_near_forward(self):
        r = self._result()
        assert r["rn_mean"] == pytest.approx(self.F, abs=0.5)

    def test_prob_ladder_matches_n_d2(self):
        r = self._result()
        for key, p in r["prob_ladder"].items():
            k = float(key.split("_")[-1])
            assert p == pytest.approx(_n_d2(self.F, k, self.T, self.VOL), abs=0.02), key

    def test_expected_move_matches_straddle(self):
        r = self._result()
        c = black76_price(self.F, self.F, self.T, self.R, self.VOL, "C")
        p = black76_price(self.F, self.F, self.T, self.R, self.VOL, "P")
        expected = (c + p) * math.exp(self.R * self.T)
        assert r["expected_move_straddle"] == pytest.approx(expected, abs=0.05)

    def test_lognormal_positive_skew(self):
        # a lognormal density is right-skewed
        assert self._result()["rn_skew"] > 0

    def test_product_compute_converts_american_prices_to_european_equivalents(self):
        rows = _synthetic_rows(self.F, self.T, self.R, self.VOL)
        table = pa.Table.from_pylist([
            {
                "option_expiry": "2026-08-17",
                "strike": row["strike"],
                "call_put": row["cp"],
                "settlement": row["settlement"],
                "forward_price": self.F,
                "time_to_expiry_years": self.T,
                "baw_iv": self.VOL,
            }
            for row in rows
        ])

        result = compute(table, "2026-07-20", rate=self.R, n_expiries=1)["expiries"][0]

        assert result["exercise_adjustment"] == "baw_iv_to_black76"
        assert result["exercise_adjusted_rows"] == len(rows)
        assert result["method"].startswith("otm_european_equivalent")


class TestGuards:
    def test_too_few_strikes_none(self):
        rows = _synthetic_rows(step=10.0)  # only ~8 strikes
        assert compute_expiry(rows, 70.0, 0.15, 0.05) is None

    def test_prob_above_edges(self):
        density = [(60.0, 0.0), (70.0, 0.1), (80.0, 0.0)]  # triangle, mass 1
        assert _prob_above(density, 55.0) == pytest.approx(1.0, abs=1e-9)
        assert _prob_above(density, 85.0) == 0.0
        assert _prob_above(density, 70.0) == pytest.approx(0.5, abs=1e-9)

    def test_duplicate_strike_side_withholds_probabilities(self):
        rows = _synthetic_rows()
        rows.append({"strike": 75.0, "cp": "C", "settlement": 99.0})
        result = compute_expiry(rows, 70.0, 0.15, 0.05)
        assert result["status"] == "invalid_surface"
        assert result["duplicate_rows"] == 1
        assert result["prob_ladder"] == {}

    def test_material_negative_density_withholds_probabilities(self):
        rows = _synthetic_rows()
        for row in rows:
            if row["cp"] == "C" and row["strike"] == 75.0:
                row["settlement"] += 5.0
        result = compute_expiry(rows, 70.0, 0.15, 0.05)
        assert result["status"] == "invalid_surface"
        assert result["negative_mass"] > 0.05
        assert result["rn_mean"] is None

    def test_tick_rounded_dense_surface_is_repaired_when_adjustment_is_small(self):
        rows = _synthetic_rows(
            F=4000.0, T=0.05, vol=0.20, lo=2500, hi=5500, step=5.0,
        )
        for row in rows:
            row["settlement"] = round(row["settlement"] / 0.10) * 0.10

        result = compute_expiry(
            rows, 4000.0, 0.05, 0.05,
            price_tick=0.10, max_projection_ticks=2.0,
        )

        assert result["status"] == "available"
        assert result["negative_mass"] > 0.05
        assert result["projection_applied"] is True
        assert result["projection_max_adjustment_ticks"] <= 2.0
        assert result["projected_mass"] == pytest.approx(1.0, abs=0.05)
        assert result["rn_mean"] == pytest.approx(4000.0, abs=10.0)
        assert result["validation_warnings"]

    def test_fitted_distribution_is_nonnegative_normalized_and_forward_consistent(self):
        rows = _synthetic_rows()
        result = compute_expiry(
            rows, 70.0, 0.15, 0.05,
            price_tick=0.01, max_projection_ticks=2.0,
        )

        assert result["status"] == "available"
        points = result["density_points"]
        assert all(point["density"] >= 0 for point in points)
        assert sum(point["probability_mass"] for point in points) == pytest.approx(1.0, abs=1e-6)
        assert result["fitted_forward"] == pytest.approx(70.0, abs=0.01)
        assert result["tail_boundary_mass"] < 0.025

    def test_probability_outputs_scale_with_distribution_not_fixed_dollars(self):
        rows = _synthetic_rows(
            F=4000.0, T=0.25, vol=0.20, lo=2000, hi=6500, step=10.0,
        )
        result = compute_expiry(
            rows, 4000.0, 0.25, 0.05,
            price_tick=0.10, max_projection_ticks=2.0,
        )

        thresholds = sorted(float(key.removeprefix("p_above_")) for key in result["prob_ladder"])
        assert thresholds[-1] - thresholds[0] > 1000.0
        assert sum(bucket["probability"] for bucket in result["probability_buckets"]) \
            == pytest.approx(1.0, abs=1e-3)
        assert list(result["quantiles"].values()) == sorted(result["quantiles"].values())

    def test_tick_aware_projection_still_rejects_material_price_corruption(self):
        rows = _synthetic_rows()
        for row in rows:
            if row["cp"] == "C" and row["strike"] == 75.0:
                row["settlement"] += 5.0

        result = compute_expiry(
            rows, 70.0, 0.15, 0.05,
            price_tick=0.10, max_projection_ticks=2.0,
        )

        assert result["status"] == "invalid_surface"
        assert result["projection_max_adjustment_ticks"] > 2.0
        assert result["prob_ladder"] == {}

    def test_call_put_regime_mismatch_cannot_be_smoothed_into_probabilities(self):
        rows = _synthetic_rows()
        for row in rows:
            if row["cp"] == "C":
                row["settlement"] = black76_price(
                    70.0, row["strike"], 0.15, 0.05, 0.45, "C"
                )

        result = compute_expiry(
            rows, 70.0, 0.15, 0.05,
            price_tick=0.01, max_projection_ticks=2.0,
        )

        assert result["status"] == "invalid_surface"
        assert result["fit_max_residual_ticks"] > 2.0
        assert result["prob_ladder"] == {}

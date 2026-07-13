"""Tests for EIA seasonal features (B4)."""
from __future__ import annotations

from ccvm.analytics.eia_seasonal import (
    _trigger_from_draw,
    _weekly_changes,
    seasonal_stats,
)


class TestTriggerThresholds:
    def test_bands(self):
        assert _trigger_from_draw(3500) == "bull_confirmed"
        assert _trigger_from_draw(1000) == "none"
        assert _trigger_from_draw(-2500) == "bear_watch"
        assert _trigger_from_draw(-4500) == "bear_confirmed"


class TestWeeklyChanges:
    def test_consecutive_diffs(self):
        levels = {"2026-06-19": 100.0, "2026-06-26": 104.0, "2026-07-03": 101.0}
        ch = _weekly_changes(levels)
        assert ch == {"2026-06-26": 4.0, "2026-07-03": -3.0}


class TestSeasonalStats:
    def _changes(self):
        # week-27-ish observations across 4 prior years + current year
        out = {}
        for y, v in [(2022, -1000.0), (2023, -500.0), (2024, -800.0), (2025, -700.0)]:
            out[f"{y}-07-05"] = v
        out["2026-07-03"] = 3000.0   # current — must be excluded from the band
        out["2024-01-05"] = 9000.0   # wrong season — excluded
        return out

    def test_prior_years_same_week_only(self):
        st = seasonal_stats(self._changes(), "2026-07-03")
        assert st["n_samples"] == 4
        assert st["avg_change"] == (-1000 - 500 - 800 - 700) / 4

    def test_insufficient_samples_none(self):
        ch = {"2025-07-04": -500.0, "2026-07-03": 3000.0}
        assert seasonal_stats(ch, "2026-07-03") is None

    def test_week_wraparound(self):
        # week 1 vs week 52 should count as adjacent (min(d, 52-d) <= 1)
        ch = {"2023-12-29": 1.0, "2024-12-27": 2.0, "2025-01-03": 3.0,
              "2026-01-02": 0.0}
        st = seasonal_stats(ch, "2026-01-02")
        assert st is not None and st["n_samples"] == 3

"""Tests for machine-readable triggers + scenario state machine (C1/D2)."""
from __future__ import annotations

from ccvm.analytics.triggers import (
    TRIGGERS,
    check_breaks_band,
    check_change_over_sessions,
    check_consecutive_eia,
    check_range_over_sessions,
    check_threshold,
    evaluate_triggers,
)
from ccvm.analytics.monitor_state import _status_from_triggers


def _settle_series(vals: dict) -> dict:
    return {"front_settle": vals}


class TestChecks:
    def test_threshold(self):
        s = {"rr25": {"2026-07-09": 0.031}}
        assert check_threshold(s, "2026-07-09", "rr25", ">=", 0.03) is True
        assert check_threshold(s, "2026-07-09", "rr25", "<=", -0.05) is False
        assert check_threshold(s, "2026-07-08", "rr25", ">=", 0.03) is None  # no data

    def test_breaks_band_above(self):
        s = _settle_series({
            "2026-07-01": 70.0, "2026-07-02": 71.0, "2026-07-06": 72.0,
            "2026-07-09": 73.0,
        })
        assert check_breaks_band(s, "2026-07-09", "front_settle", 30, "above") is True
        s["front_settle"]["2026-07-09"] = 71.5
        assert check_breaks_band(s, "2026-07-09", "front_settle", 30, "above") is False

    def test_breaks_band_excludes_today_and_needs_history(self):
        # only 2 prior points → not evaluable
        s = _settle_series({"2026-07-06": 72.0, "2026-07-08": 71.0, "2026-07-09": 75.0})
        assert check_breaks_band(s, "2026-07-09", "front_settle", 30, "above") is None

    def test_change_over_sessions(self):
        s = _settle_series({f"2026-07-0{i}": 70.0 + i for i in range(1, 10)})
        # today 79 vs 5 sessions ago 74 → +5; "< 0" false, ">4 abs" true
        assert check_change_over_sessions(s, "2026-07-09", "front_settle", 5, "<", 0.0) is False
        assert check_change_over_sessions(s, "2026-07-09", "front_settle", 5, ">", 4.0, absolute=True) is True
        # insufficient sessions
        assert check_change_over_sessions(s, "2026-07-03", "front_settle", 5, "<", 0.0) is None

    def test_range_over_sessions(self):
        s = _settle_series({"2026-07-06": 70.0, "2026-07-07": 71.0, "2026-07-08": 69.5,
                            "2026-07-09": 70.5, "2026-07-10": 70.2})
        assert check_range_over_sessions(s, "2026-07-10", "front_settle", 5, "<=", 6.0) is True
        assert check_range_over_sessions(s, "2026-07-10", "front_settle", 5, "<=", 1.0) is False

    def test_consecutive_eia(self):
        s = {"eia_periods": [("2026-06-19", -2500.0), ("2026-06-26", -3000.0)]}
        assert check_consecutive_eia(s, "x", "<", -2000, 2) is True   # two builds > 2mb
        assert check_consecutive_eia(s, "x", ">", 3000, 1) is False   # latest is a build
        assert check_consecutive_eia(s, "x", "<", -2000, 3) is None   # not enough periods


class TestEvaluateAndStateMachine:
    def _bull_confirmed_series(self):
        settles = {f"2026-06-{d:02d}": 65.0 + d * 0.1 for d in range(1, 30)}
        settles["2026-07-09"] = 75.0  # breaks the prior band high
        return {
            "front_settle": settles,
            "curve_slope": {"2026-07-09": -0.15},
            "atm_iv": {"2026-07-09": 0.22},
            "rr25": {"2026-07-09": 0.035},
            "eia_periods": [("2026-06-26", 3775.0)],
        }

    def test_bull_confirms_fire(self):
        results = evaluate_triggers(self._bull_confirmed_series(), "2026-07-09")
        by_id = {r["id"]: r["fired"] for r in results}
        assert by_id["bull_c_30d_high"] is True
        assert by_id["bull_c_call_skew"] is True
        assert by_id["bull_c_eia_draw"] is True
        assert by_id["bull_i_contango"] is False
        assert by_id["bear_i_opec_cut"] is None  # manual never auto-fires

    def test_status_confirmed(self):
        results = evaluate_triggers(self._bull_confirmed_series(), "2026-07-09")
        status, detail = _status_from_triggers(results, "bull")
        assert status == "confirmed"
        assert len(detail["confirms_fired"]) >= 2

    def test_invalidation_wins(self):
        series = self._bull_confirmed_series()
        series["curve_slope"]["2026-07-09"] = 1.5  # contango > $1/mo → invalidates bull
        results = evaluate_triggers(series, "2026-07-09")
        status, detail = _status_from_triggers(results, "bull")
        assert status == "invalidated"
        assert "bull_i_contango" in detail["invalidations_fired"]

    def test_all_triggers_well_formed(self):
        for t in TRIGGERS:
            assert t["scenario"] in ("bull", "base", "bear")
            assert t["side"] in ("confirm", "invalidate")
            assert t["kind"] in ("auto", "manual")
            if t["kind"] == "auto":
                assert t["check"] is not None

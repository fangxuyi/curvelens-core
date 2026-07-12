"""Tests for OI analytics (C2) and realized vol (B6)."""
from __future__ import annotations

import math

import pyarrow as pa
import pytest

from ccvm.analytics.history_context import realized_vol
from ccvm.analytics.oi_features import compute, max_pain, _walls, _delta_oi, _rows


def _chain(expiry="2026-08-17", rows=None):
    rows = rows or []
    return pa.table({
        "option_expiry": [r[0] for r in rows],
        "strike": [r[1] for r in rows],
        "call_put": [r[2] for r in rows],
        "open_interest": [r[3] for r in rows],
        "volume": [r[4] for r in rows],
        "silver_status": [r[5] if len(r) > 5 else "PASS" for r in rows],
    })


_E = "2026-08-17"
_BASIC = [
    (_E, 65.0, "P", 1000, 50),
    (_E, 70.0, "P", 500, 20),
    (_E, 70.0, "C", 800, 100),
    (_E, 75.0, "C", 2000, 30),
]


class TestMaxPain:
    def test_all_calls_pin_lowest(self):
        rows = _rows(_chain(rows=[(_E, 70.0, "C", 100, 0), (_E, 75.0, "C", 100, 0)]))
        # Only calls: payout minimized at the lowest strike
        assert max_pain(rows) == 70.0

    def test_balanced_chain(self):
        rows = _rows(_chain(rows=_BASIC))
        # candidates: 65 → call pay 0+0, put pay 0+2500=... compute: at 65:
        # puts: 65P 0, 70P (70-65)*500=2500; calls 0 → 2500
        # at 70: puts 0; calls 0; 65P 0 → 0?? calls: 70C 0, 75C 0; puts: 70P 0
        # at 70 total payout = 0 (65P OTM, 70P at strike, calls at/below strike)
        assert max_pain(rows) == 70.0

    def test_empty(self):
        assert max_pain([]) is None


class TestWallsAndDelta:
    def test_walls_sorted_by_oi(self):
        rows = _rows(_chain(rows=_BASIC))
        cw = _walls(rows, "C")
        assert cw[0] == {"strike": 75.0, "oi": 2000}
        pw = _walls(rows, "P")
        assert pw[0]["strike"] == 65.0

    def test_delta_oi(self):
        today = _rows(_chain(rows=_BASIC))
        prior = _rows(_chain(rows=[(_E, 75.0, "C", 1500, 0), (_E, 65.0, "P", 1200, 0)]))
        d = _delta_oi(today, prior)
        by_key = {(x["cp"], x["strike"]): x["delta_oi"] for x in d}
        assert by_key[("C", 75.0)] == 500       # 2000 - 1500
        # 70C is new (prior missing → baseline 0) = +800
        assert by_key.get(("C", 70.0), 800) == 800

    def test_no_prior_no_deltas(self):
        assert _delta_oi(_rows(_chain(rows=_BASIC)), []) == []


class TestCompute:
    def test_front_expiry_summary(self):
        out = compute(_chain(rows=_BASIC), "2026-07-09")
        e0 = out["expiries"][0]
        assert e0["expiry"] == _E
        assert e0["call_oi"] == 2800 and e0["put_oi"] == 1500
        assert e0["put_call_oi_ratio"] == pytest.approx(1500 / 2800, abs=1e-3)
        assert e0["max_pain"] == 70.0

    def test_fail_rows_excluded(self):
        rows = _BASIC + [(_E, 80.0, "C", 99999, 0, "FAIL")]
        out = compute(_chain(rows=rows), "2026-07-09")
        assert out["expiries"][0]["call_oi"] == 2800  # FAIL row ignored


class TestRealizedVol:
    def test_constant_prices_zero_vol(self):
        assert realized_vol([70.0] * 12, 10) == pytest.approx(0.0)

    def test_insufficient_history(self):
        assert realized_vol([70.0] * 10, 10) is None  # needs window+1

    def test_known_value(self):
        # alternating ±1% daily log-ish moves → annualized ≈ 1% * sqrt(252)
        s = [100.0]
        for i in range(10):
            s.append(s[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
        rv = realized_vol(s, 10)
        assert rv == pytest.approx(0.01 * math.sqrt(252), rel=0.05)

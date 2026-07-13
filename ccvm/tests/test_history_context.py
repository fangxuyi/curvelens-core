"""Tests for analytics/history_context.py (B2)."""
from __future__ import annotations

import pyarrow as pa
import pytest

from ccvm.analytics.history_context import (
    _futures_metrics,
    _options_metrics,
    percentile_of,
    zscore_of,
)


class TestStats:
    def test_percentile_needs_min_obs(self):
        assert percentile_of([1, 2, 3], 2) is None          # < 5 obs
        assert percentile_of([1, 2, 3, 4, 5], 5) == 100.0
        assert percentile_of([1, 2, 3, 4, 5], 1) == 20.0    # inclusive
        assert percentile_of([1, 2, 3, 4, 5], 3) == 60.0

    def test_zscore(self):
        assert zscore_of([1, 2, 3], 2) is None
        assert zscore_of([2, 2, 2, 2, 2], 2) == 0.0          # zero variance
        z = zscore_of([1, 2, 3, 4, 5], 5)
        assert z == pytest.approx((5 - 3) / (2 ** 0.5), rel=1e-9)

    def test_percentile_monotone(self):
        vals = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        ps = [percentile_of(vals, v) for v in vals]
        assert ps == sorted(ps)


class TestMetricExtraction:
    def test_futures_front_row(self):
        t = pa.table({
            "contract_code": ["CLQ26", "CLU26"],
            "settlement": [68.7, 68.6],
            "front_back_slope": [-0.15, -0.15],
            "spread_to_next": [-0.04, -0.05],
        })
        m = _futures_metrics(t)
        assert m["front_settle"] == 68.7
        assert m["curve_slope"] == -0.15
        assert m["m1_m2_spread"] == -0.04

    def test_options_front_expiry_selected(self):
        # rows deliberately ordered with a back expiry first
        t = pa.table({
            "option_expiry": ["2026-09-17", "2026-08-17", "2026-08-17"],
            "atm_iv": [0.20, 0.215, 0.215],
            "risk_reversal_25d": [0.02, 0.03, 0.03],
            "butterfly_25d": [0.01, 0.011, 0.011],
            "skew_slope": [-0.07, -0.09, -0.09],
        })
        m = _options_metrics(t)
        assert m["atm_iv"] == 0.215     # front expiry (2026-08-17), not first row
        assert m["rr25"] == 0.03

    def test_empty_tables(self):
        assert _futures_metrics(pa.table({"contract_code": pa.array([], pa.string())})) == {}
        assert _options_metrics(pa.table({"option_expiry": pa.array([], pa.string())})) == {}


class TestBrentSpread:
    def test_find_and_load_raw_brent(self, tmp_path):
        from datetime import date
        import json as _json
        from ccvm.collectors.yfinance_brent import find_raw_brent, load_brent_closes
        d = tmp_path / "raw" / "yfinance_brent_front" / "2026-07-10"
        d.mkdir(parents=True)
        (d / "brent_front_20260710.json").write_text(
            _json.dumps({"ticker": "BZ=F", "closes": {"2026-07-10": 76.01}}))
        assert find_raw_brent(tmp_path, date(2026, 7, 10)) is not None
        assert find_raw_brent(tmp_path, date(2026, 7, 9)) is None  # file is dated after
        assert load_brent_closes(tmp_path, date(2026, 7, 10)) == {"2026-07-10": 76.01}
        assert load_brent_closes(tmp_path, date(2026, 7, 9)) == {}

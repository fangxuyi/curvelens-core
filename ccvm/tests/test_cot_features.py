"""Tests for COT positioning features (B3)."""
from __future__ import annotations

import json
from datetime import date

from ccvm.analytics.cot_features import compute
from ccvm.collectors.cftc_cot import find_raw_cot, load_cot_rows


def _write_raw(tmp_path, rows, fname="cftc_cot_wti_20260710.json", day="2026-07-10"):
    d = tmp_path / "raw" / "cftc_cot_wti" / day
    d.mkdir(parents=True)
    (d / fname).write_text(json.dumps({"contract": "WTI", "rows": rows}))


_ROWS = [
    {"report_date": "2026-06-23", "mm_long": 150000, "mm_short": 100000,
     "prod_long": 700000, "prod_short": 300000, "open_interest": 2500000},
    {"report_date": "2026-06-30", "mm_long": 199357, "mm_short": 105644,
     "prod_long": 709925, "prod_short": 325612, "open_interest": 2568532},
    {"report_date": "2026-07-07", "mm_long": 186489, "mm_short": 111810,
     "prod_long": 734977, "prod_short": 330736, "open_interest": 2560340},
]


class TestLoad:
    def test_find_respects_as_of(self, tmp_path):
        _write_raw(tmp_path, _ROWS)
        assert find_raw_cot(tmp_path, date(2026, 7, 10)) is not None
        assert find_raw_cot(tmp_path, date(2026, 7, 9)) is None

    def test_load_rows(self, tmp_path):
        _write_raw(tmp_path, _ROWS)
        assert len(load_cot_rows(tmp_path, date(2026, 7, 10))) == 3


class TestCompute:
    def test_latest_and_wow(self, tmp_path):
        _write_raw(tmp_path, _ROWS)
        c = compute(tmp_path, "2026-07-10")
        assert c["report_date"] == "2026-07-07"
        assert c["mm_net"] == 186489 - 111810          # +74,679
        assert c["mm_net_wow"] == 74679 - (199357 - 105644)   # −19,034
        assert c["prod_net"] == 734977 - 330736

    def test_reports_after_as_of_excluded(self, tmp_path):
        _write_raw(tmp_path, _ROWS)
        # as-of before the 07-07 report → latest usable is 06-30
        c = compute(tmp_path, "2026-07-10")
        rows_before = [r for r in _ROWS if r["report_date"] <= "2026-07-05"]
        # emulate: compute for a date where only 2 reports existed
        _write_raw(tmp_path.__class__(str(tmp_path)) if False else tmp_path, rows_before,
                   fname="cftc_cot_wti_20260705.json", day="2026-07-05")
        c2 = compute(tmp_path, "2026-07-05")
        assert c2["report_date"] == "2026-06-30"

    def test_no_data(self, tmp_path):
        assert compute(tmp_path, "2026-07-10") is None

    def test_percentile_none_with_short_history(self, tmp_path):
        _write_raw(tmp_path, _ROWS)  # 3 weeks < _MIN_OBS
        c = compute(tmp_path, "2026-07-10")
        assert c["mm_net_pctile_1y"] is None

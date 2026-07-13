"""Tests for the calibration scorecard (C7)."""
from __future__ import annotations

import json

import pyarrow as pa
import pytest

from ccvm.analytics.scorecard import compute


class _FakePQ:
    """Minimal ParquetStore stand-in: settle series keyed by date."""
    def __init__(self, settles: dict[str, float]):
        self.settles = settles

    def list_dates(self, layer, dataset):
        return sorted(self.settles)

    def read(self, layer, dataset, dt):
        return pa.table({"settlement": [self.settles[dt]]})


def _setup(tmp_path, settles, states):
    for dt, st in states.items():
        p = tmp_path / "gold" / "agreement" / f"trade_date={dt}"
        p.mkdir(parents=True)
        (p / "agreement.json").write_text(json.dumps({"state": st}))
    return _FakePQ(settles)


def test_hit_rate_and_forward_returns(tmp_path):
    # 8 sessions, rising steadily: upside days should be hits
    dates = [f"2026-07-{d:02d}" for d in range(1, 9)]
    settles = {dt: 70.0 + i for i, dt in enumerate(dates)}
    states = {dt: "confirmed_upside_risk" for dt in dates[:5]}
    pq = _setup(tmp_path, settles, states)

    out = compute(pq, tmp_path, dates[-1])
    row = next(r for r in out["states"] if r["state"] == "confirmed_upside_risk")
    assert row["n"] == 5
    assert row["hit_rate_3d"] == 1.0            # monotone rise → all hits
    assert row["avg_fwd_3d"] == pytest.approx(3 / 71.5, rel=0.1)
    assert (tmp_path / "state" / "scorecard.json").exists()


def test_downside_direction_and_edge(tmp_path):
    dates = [f"2026-07-{d:02d}" for d in range(1, 7)]
    settles = {dt: 80.0 - 2 * i for i, dt in enumerate(dates)}
    states = {dates[0]: "confirmed_downside_risk", dates[-1]: "confirmed_downside_risk"}
    pq = _setup(tmp_path, settles, states)

    out = compute(pq, tmp_path, dates[-1])
    row = next(r for r in out["states"] if r["state"] == "confirmed_downside_risk")
    # last date has no forward window → only the first contributes to hits
    assert row["n"] == 2 and row["n_hits_3d"] == 1
    assert row["hit_rate_3d"] == 1.0            # falling market, downside call = hit


def test_nondirectional_states_no_hit_rate(tmp_path):
    dates = [f"2026-07-{d:02d}" for d in range(1, 6)]
    settles = {dt: 70.0 for dt in dates}
    states = {dates[0]: "no_material_change"}
    pq = _setup(tmp_path, settles, states)
    out = compute(pq, tmp_path, dates[-1])
    row = out["states"][0]
    assert row["hit_rate_3d"] is None and row["avg_fwd_3d"] == 0.0

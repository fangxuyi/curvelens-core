"""Tests for the calibration scorecard (C7)."""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys

import pyarrow as pa
import pytest

from ccvm.analytics.scorecard import compute


class _FakePQ:
    """Minimal ParquetStore stand-in: settle series keyed by date."""
    def __init__(self, settles: dict[str, float]):
        self.settles = settles
        self.layers = []

    def list_dates(self, layer, dataset):
        self.layers.append(layer)
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


def test_scorecard_is_product_and_storage_layer_driven(tmp_path):
    settles = {"2026-07-01": 100, "2026-07-02": 101,
               "2026-07-03": 102, "2026-07-04": 103}
    states = {"2026-07-01": "confirmed_upside_risk"}
    for dt, state in states.items():
        path = tmp_path / "copper" / "agreement" / f"trade_date={dt}"
        path.mkdir(parents=True)
        (path / "agreement.json").write_text(json.dumps({"state": state}))
    pq = _FakePQ(settles)
    result = compute(
        pq, tmp_path, "2026-07-04", product="copper", layer="shared_features",
    )
    assert result["product"] == "copper"
    assert pq.layers == ["shared_features"]
    assert result["states"][0]["state"] == "confirmed_upside_risk"


@pytest.mark.parametrize("product", ["wti", "gold", "corn", "silver", "copper", "brent"])
def test_daily_report_labels_scorecard_with_active_product(tmp_path, monkeypatch, product):
    from ccvm.analytics import monitor_state, scorecard
    from ccvm.reference.product import get_product
    monkeypatch.setenv("CCVM_PRODUCT", product)
    get_product.cache_clear()
    dates = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
    pq = _setup(tmp_path, dict(zip(dates, [100., 101., 102., 103.])),
                {dates[0]: "confirmed_upside_risk"})
    pq.exists = lambda layer, dataset, dt: dataset == "futures_features"
    script = Path(__file__).resolve().parents[1] / "scripts/generate_report.py"
    spec = importlib.util.spec_from_file_location("scorecard_report_entrypoint", script)
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    monkeypatch.setattr(report, "DATA_DIR", tmp_path)
    monkeypatch.setattr(report, "ParquetStore", lambda _: pq)
    monkeypatch.setattr(monitor_state, "compute_streaks", lambda *a: {})
    monkeypatch.setattr(monitor_state, "build_day_diff", lambda *a: {})
    monkeypatch.setattr(sys, "argv", [str(script), "--date", dates[-1]])
    outputs = []
    class ScorecardReached(Exception):
        pass
    def capture(*args, **kwargs):
        outputs.append(compute(*args, **kwargs))
        raise ScorecardReached
    monkeypatch.setattr(scorecard, "compute", capture)
    try:
        with pytest.raises(ScorecardReached):
            report.main()
        assert outputs[0]["product"] == product
        assert outputs[0]["states"][0]["hit_rate_3d"] == 1.0
        assert pq.layers == ["gold"]
    finally:
        get_product.cache_clear()

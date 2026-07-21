"""Profile-driven macro normalization and interpretation."""
from __future__ import annotations

import json
from datetime import date

import pyarrow as pa
import pytest

from ccvm.analytics.macro_context import compute, normalize_fred
from ccvm.reference.product import MacroSeriesSpec


def _futures() -> pa.Table:
    return pa.table({
        "settlement": [2400.0, 2412.0],
        "contract_code": ["GCQ26", "GCV26"],
    })


def test_normalize_fred_omits_missing_and_future(tmp_path, monkeypatch):
    import ccvm.analytics.macro_context as module
    monkeypatch.setattr(module, "get_product", lambda: type("P", (), {"key": "gold"})())
    raw = tmp_path / "fred.json"
    raw.write_text(json.dumps({"observations": [
        {"date": "2026-07-16", "value": "1.20"},
        {"date": "2026-07-17", "value": "."},
        {"date": "2026-07-21", "value": "1.10"},
    ]}))
    spec = MacroSeriesSpec("real_yield_10y", "DFII10", "Real yield", "percent",
                           "opportunity_cost", -1)
    table = normalize_fred(raw, "abc", spec, date(2026, 7, 20))
    assert len(table) == 1
    assert table.to_pydict()["value"] == [1.2]


def test_compute_flat_price_carry_and_vol():
    silver = pa.table({
        "as_of_date": ["2026-07-20"] * 6,
        "series_key": ["real_yield_10y", "real_yield_10y", "broad_usd",
                       "broad_usd", "treasury_3m", "treasury_3m"],
        "series_id": ["DFII10", "DFII10", "DTWEXBGS", "DTWEXBGS",
                      "DGS3MO", "DGS3MO"],
        "label": ["Real yield", "Real yield", "Dollar", "Dollar", "3m", "3m"],
        "units": ["percent", "percent", "index", "index", "percent", "percent"],
        "role": ["opportunity_cost"] * 2 + ["currency"] * 2 + ["financing_carry"] * 2,
        "flat_price_sign": [-1, -1, -1, -1, 0, 0],
        "observation_date": ["2026-07-17", "2026-07-20"] * 3,
        "value": [1.20, 1.15, 122.0, 121.0, 4.0, 4.0],
        "source_id": ["fred"] * 6,
        "raw_sha256": ["abc"] * 6,
    })
    options = pa.table({
        "atm_iv": [0.20], "risk_reversal_25d": [0.03], "butterfly_25d": [0.01],
    })
    result = compute(silver, _futures(), options)
    assert result["flat_price"]["directional_prior"] == "supportive"
    assert result["flat_price"]["score"] == 2
    assert result["curve"]["implied_carry"] == pytest.approx(0.06)
    assert result["curve"]["carry_gap"] == pytest.approx(0.02)
    assert result["vol_surface"]["interpretation"] == "call_skew"


def test_fred_collector_skips_without_key(monkeypatch, tmp_path):
    from ccvm.collectors.fred_macro import FREDMacroCollector
    from ccvm.reference.product import get_product
    from ccvm.storage.manifest_db import ManifestDB
    from ccvm.storage.raw_store import RawStore

    import ccvm.collectors.fred_macro as module
    monkeypatch.setattr(module, "get_product", lambda: get_product("gold"))
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    collector = FREDMacroCollector(RawStore(tmp_path), ManifestDB(tmp_path / "m.duckdb"))
    result = collector.collect(date(2026, 7, 20))
    assert result["status"] == "skipped"
    assert result["skipped"] == 5

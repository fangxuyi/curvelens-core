"""Tests for scenario engine and daily report generator."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

from ccvm.scenarios.scenario_engine import (
    ScenarioShock, apply_shock, apply_vol_shock, generate, to_dict
)
from ccvm.reporting.daily_report import generate as gen_report

AS_OF = date(2026, 6, 25)


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def _gold_futures(settlements: list[float]) -> pa.Table:
    from ccvm.analytics.futures_features import _SCHEMA
    n = len(settlements)
    rows = {f.name: [] for f in _SCHEMA}
    for i, s in enumerate(settlements):
        rows["trade_date"].append("2026-06-25")
        rows["contract_code"].append(f"CLQ{26+i}")
        rows["delivery_month"].append(f"2026-{8+i:02d}")
        rows["settlement"].append(s)
        rows["curve_position"].append(i + 1)
        rows["days_to_expiry"].append(27 + i * 30)
        rows["return_1d"].append(None)
        rows["spread_to_next"].append(None if i == n - 1 else settlements[i+1] - s)
        rows["butterfly"].append(None)
        rows["front_back_slope"].append((settlements[-1] - settlements[0]) / max(n-1, 1))
        rows["contango_flag"].append(settlements[-1] > settlements[0])
        rows["source_id"].append("yfinance_wti_futures")
    return pa.table(rows, schema=_SCHEMA)


def _gold_options() -> pa.Table:
    from ccvm.analytics.option_features import _SCHEMA
    rows = {f.name: [] for f in _SCHEMA}
    for j in range(6):
        strike = 70.0 + j * 2.5
        for cp in ("C", "P"):
            rows["trade_date"].append("2026-06-25")
            rows["option_expiry"].append("2026-08-21")
            rows["option_symbol"].append(f"X{j}{cp}")
            rows["underlying_contract"].append("USO")
            rows["strike"].append(strike)
            rows["call_put"].append(cp)
            rows["settlement"].append(2.0)
            rows["forward_price"].append(75.0)
            rows["time_to_expiry_years"].append(0.2)
            rows["moneyness_log"].append(0.05)
            rows["black76_iv"].append(0.30)
            rows["black76_delta"].append(0.5 if cp == "C" else -0.5)
            rows["black76_vega"].append(0.10)
            rows["baw_iv"].append(0.30)
            rows["baw_delta"].append(0.5 if cp == "C" else -0.5)
            rows["baw_vega"].append(0.10)
            rows["early_exercise_premium"].append(0.0)
            rows["market_delta"].append(0.5)
            rows["atm_iv"].append(0.30)
            rows["iv_25d_call"].append(0.31)
            rows["iv_25d_put"].append(0.29)
            rows["risk_reversal_25d"].append(0.02)
            rows["butterfly_25d"].append(0.00)
            rows["skew_slope"].append(-0.01)
            rows["valid_call_strikes"].append(6)
            rows["valid_put_strikes"].append(6)
            rows["coverage_status"].append("PASS")
            rows["source_id"].append("etrade_uso_options")
            rows["price_note"].append("USO_proxy")
    return pa.table(rows, schema=_SCHEMA)


# ──────────────────────────────────────────────────────────
# apply_shock
# ──────────────────────────────────────────────────────────

def test_apply_shock_parallel_shift():
    contracts = [
        {"contract_code": "CLQ26", "delivery_month": "2026-08", "settlement": 72.0, "curve_position": 1},
        {"contract_code": "CLU26", "delivery_month": "2026-09", "settlement": 71.5, "curve_position": 2},
    ]
    shock = ScenarioShock(name="test", description="", curve_shift_usd=5.0, curve_tilt=0.0, vol_shift_pct=0.0)
    result = apply_shock(contracts, shock, AS_OF)
    assert result[0]["shocked_settlement"] == pytest.approx(77.0, abs=0.01)
    assert result[1]["shocked_settlement"] == pytest.approx(76.5, abs=0.01)


def test_apply_shock_tilt():
    contracts = [
        {"contract_code": "CLQ26", "delivery_month": "2026-08", "settlement": 72.0, "curve_position": 1},
        {"contract_code": "CLU26", "delivery_month": "2026-09", "settlement": 71.5, "curve_position": 2},
        {"contract_code": "CLV26", "delivery_month": "2026-10", "settlement": 71.0, "curve_position": 3},
    ]
    # Tilt of -1.0 $/position: front unchanged, each step goes down by 1
    shock = ScenarioShock(name="test", description="", curve_shift_usd=0.0, curve_tilt=-1.0, vol_shift_pct=0.0)
    result = apply_shock(contracts, shock, AS_OF)
    assert result[0]["diff"] == pytest.approx(0.0, abs=0.01)  # position 1: 0 × tilt
    assert result[1]["diff"] == pytest.approx(-1.0, abs=0.01)  # position 2: 1 × tilt
    assert result[2]["diff"] == pytest.approx(-2.0, abs=0.01)  # position 3: 2 × tilt


def test_apply_vol_shock():
    expiry_ivs = [
        {"option_expiry": "2026-08-21", "atm_iv": 0.30},
        {"option_expiry": "2026-09-18", "atm_iv": 0.28},
    ]
    shock = ScenarioShock(name="test", description="", curve_shift_usd=0.0, curve_tilt=0.0, vol_shift_pct=0.05)
    result = apply_vol_shock(expiry_ivs, shock)
    assert result[0]["shocked_iv"] == pytest.approx(0.35, abs=0.001)
    assert result[1]["shocked_iv"] == pytest.approx(0.33, abs=0.001)
    assert result[0]["diff_pp"] == pytest.approx(5.0, abs=0.1)


def test_apply_vol_shock_floor_at_one_pct():
    expiry_ivs = [{"option_expiry": "2026-08-21", "atm_iv": 0.03}]
    shock = ScenarioShock(name="test", description="", curve_shift_usd=0.0, curve_tilt=0.0, vol_shift_pct=-0.05)
    result = apply_vol_shock(expiry_ivs, shock)
    assert result[0]["shocked_iv"] >= 0.01


# ──────────────────────────────────────────────────────────
# generate scenarios
# ──────────────────────────────────────────────────────────

def test_generate_produces_3_standard_scenarios():
    gf = _gold_futures([72.0, 71.5, 71.0, 70.5])
    results = generate(gf, None, AS_OF)
    assert len(results) == 3
    names = [r.name for r in results]
    assert "bull" in names
    assert "base" in names
    assert "bear" in names


def test_generate_bull_shifts_up():
    gf = _gold_futures([72.0, 71.5, 71.0])
    results = {r.name: r for r in generate(gf, None, AS_OF)}
    assert results["bull"].front_month_impact > 0
    assert results["bear"].front_month_impact < 0
    assert results["base"].front_month_impact == pytest.approx(0.0, abs=0.01)


def test_generate_includes_vol_shifts_when_options_provided():
    gf = _gold_futures([72.0, 71.5, 71.0])
    go = _gold_options()
    results = {r.name: r for r in generate(gf, go, AS_OF)}
    assert len(results["bull"].vol_shifts) > 0
    # Bull scenario raises vol
    assert results["bull"].vol_shifts[0]["shocked_iv"] > results["bull"].vol_shifts[0]["base_atm_iv"]


def test_generate_has_triggers():
    gf = _gold_futures([72.0, 71.5, 71.0])
    results = {r.name: r for r in generate(gf, None, AS_OF)}
    assert len(results["bull"].confirmation_triggers) > 0
    assert len(results["bull"].invalidation_triggers) > 0


def test_to_dict_serializable():
    gf = _gold_futures([72.0, 71.5])
    result = generate(gf, None, AS_OF)[0]
    d = to_dict(result)
    # Should be JSON serializable
    json.dumps(d)


# ──────────────────────────────────────────────────────────
# Report generation
# ──────────────────────────────────────────────────────────

def test_report_creates_files(tmp_path):
    gf = _gold_futures([72.0, 71.5, 71.0])
    go = _gold_options()
    scenarios = [to_dict(s) for s in generate(gf, go, AS_OF)]

    report = gen_report(
        trade_date=AS_OF,
        gold_futures=gf,
        gold_options=go,
        scenarios=scenarios,
        agreement={"state": "confirmed_upside_risk", "confidence": "high",
                   "evidence": ["slope=-0.50"], "inputs": {}},
        top_catalysts=[],
        quality_report={"overall_status": "PASS", "caveats": []},
        output_dir=tmp_path,
    )

    assert (tmp_path / "2026-06-25.md").exists()
    assert (tmp_path / "2026-06-25.json").exists()
    assert "trade_date" in report


def test_report_json_valid(tmp_path):
    gf = _gold_futures([72.0, 71.5, 71.0])
    scenarios = [to_dict(s) for s in generate(gf, None, AS_OF)]

    gen_report(
        trade_date=AS_OF,
        gold_futures=gf,
        gold_options=None,
        scenarios=scenarios,
        agreement={"state": "no_material_change", "confidence": "high",
                   "evidence": [], "inputs": {}},
        top_catalysts=[],
        quality_report={"overall_status": "WARN", "caveats": []},
        output_dir=tmp_path,
    )

    data = json.loads((tmp_path / "2026-06-25.json").read_text())
    assert data["trade_date"] == "2026-06-25"
    assert "sections" in data
    assert "scenarios" in data["sections"]
    assert len(data["sections"]["scenarios"]) == 3


def test_report_markdown_has_sections(tmp_path):
    gf = _gold_futures([72.0, 71.5])
    scenarios = [to_dict(s) for s in generate(gf, None, AS_OF)]

    gen_report(
        trade_date=AS_OF,
        gold_futures=gf,
        gold_options=None,
        scenarios=scenarios,
        agreement={"state": "insufficient_data", "confidence": "low",
                   "evidence": [], "inputs": {}},
        top_catalysts=[],
        quality_report={"overall_status": "INSUFFICIENT_DATA", "caveats": []},
        output_dir=tmp_path,
    )

    md = (tmp_path / "2026-06-25.md").read_text()
    for section in ["Market-Implied Risk", "Upcoming Catalysts", "Agreement",
                    "Scenarios", "Confirmation", "Caveats", "Next Review"]:
        assert section in md, f"Section '{section}' missing from report"


def test_report_includes_caveats(tmp_path):
    gf = _gold_futures([72.0])
    scenarios = [to_dict(s) for s in generate(gf, None, AS_OF)]

    report = gen_report(
        trade_date=AS_OF,
        gold_futures=gf,
        gold_options=None,
        scenarios=scenarios,
        agreement={"state": "insufficient_data", "confidence": "low",
                   "evidence": [], "inputs": {}},
        top_catalysts=[],
        quality_report={"overall_status": "INSUFFICIENT_DATA", "caveats": []},
        output_dir=tmp_path,
    )

    caveats = report["sections"]["data_caveats"]
    assert any("settlement" in c for c in caveats)
    # BAW is the pricing model for American LO options (USO caveat is long gone
    # with the dropped E*TRADE source)
    assert any("baw_iv" in c for c in caveats)
    assert any("fundamentals_cadence" in c for c in caveats)

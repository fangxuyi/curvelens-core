"""Tests for silver-layer normalizers."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

from ccvm.parsers import bronze_futures, bronze_options
from ccvm.normalizers import silver_futures, silver_options

AS_OF = date(2026, 6, 24)
SHA = "deadbeef"


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def _make_futures_bronze(settlements: list[dict]) -> pa.Table:
    import pyarrow as pa
    from ccvm.parsers.bronze_futures import _SCHEMA
    rows = {f.name: [] for f in _SCHEMA}
    for r in settlements:
        rows["trade_date"].append(r.get("trade_date", "2026-06-24"))
        rows["exchange"].append(r.get("exchange", "NYMEX"))
        rows["product"].append(r.get("product", "CL"))
        rows["contract_code"].append(r.get("contract_code", ""))
        rows["delivery_month"].append(r.get("delivery_month", ""))
        rows["settlement"].append(float(r["settlement"]) if r.get("settlement") is not None else None)
        rows["volume"].append(int(r["volume"]) if r.get("volume") is not None else None)
        rows["open_interest"].append(None)
        rows["currency"].append("USD")
        rows["price_unit"].append("USD/BBL")
        rows["source_id"].append("yfinance_wti_futures")
        rows["raw_file_sha256"].append(SHA)
    return pa.table(rows, schema=_SCHEMA)


def _make_options_bronze(settlements: list[dict]) -> pa.Table:
    from ccvm.parsers.bronze_options import _SCHEMA
    rows = {f.name: [] for f in _SCHEMA}
    for r in settlements:
        rows["trade_date"].append(r.get("trade_date", "2026-06-24"))
        rows["option_expiry"].append(r.get("option_expiry", ""))
        rows["option_symbol"].append(r.get("option_symbol", ""))
        rows["underlying_contract"].append(r.get("underlying_contract", "USO"))
        rows["underlying_delivery_month"].append(r.get("underlying_delivery_month", "2026-07"))
        rows["strike"].append(float(r["strike"]) if r.get("strike") is not None else None)
        rows["call_put"].append(r.get("call_put", "C"))
        rows["settlement"].append(float(r["settlement"]) if r.get("settlement") is not None else None)
        rows["bid"].append(r.get("bid"))
        rows["ask"].append(r.get("ask"))
        rows["volume"].append(r.get("volume"))
        rows["open_interest"].append(r.get("open_interest"))
        rows["implied_volatility"].append(r.get("implied_volatility"))
        rows["delta"].append(r.get("delta"))
        rows["gamma"].append(r.get("gamma"))
        rows["theta"].append(r.get("theta"))
        rows["vega"].append(r.get("vega"))
        rows["exercise_style"].append("American")
        rows["settlement_style"].append("Equity_ETF")
        rows["contract_multiplier"].append(100)
        rows["source_id"].append("etrade_uso_options")
        rows["price_note"].append("USO_proxy")
        rows["raw_file_sha256"].append(SHA)
    return pa.table(rows, schema=_SCHEMA)


# ──────────────────────────────────────────────────────────
# Silver futures
# ──────────────────────────────────────────────────────────

_GOOD_FUTURES = [
    {"contract_code": "CLQ26", "delivery_month": "2026-08", "settlement": 72.45, "volume": 12000},
    {"contract_code": "CLU26", "delivery_month": "2026-09", "settlement": 71.80, "volume": 5000},
    {"contract_code": "CLV26", "delivery_month": "2026-10", "settlement": 71.20, "volume": 1000},
]


def test_silver_futures_schema():
    bronze = _make_futures_bronze(_GOOD_FUTURES)
    silver = silver_futures.normalize(bronze, AS_OF)
    for field in ("last_trade_date", "cl_option_expiry", "days_to_expiry",
                  "curve_position", "silver_status", "silver_note"):
        assert field in silver.schema.names


def test_silver_futures_calendar_enrichment():
    bronze = _make_futures_bronze(_GOOD_FUTURES)
    silver = silver_futures.normalize(bronze, AS_OF)
    d = silver.to_pydict()
    # CLQ26 → LTD = 2026-07-21
    idx = d["contract_code"].index("CLQ26")
    assert d["last_trade_date"][idx] == "2026-07-21"
    assert d["cl_option_expiry"][idx] == "2026-07-13"


def test_silver_futures_curve_position():
    bronze = _make_futures_bronze(_GOOD_FUTURES)
    silver = silver_futures.normalize(bronze, AS_OF)
    d = silver.to_pydict()
    # CLQ26 should be position 1, CLU26 position 2, CLV26 position 3
    by_code = {code: pos for code, pos in zip(d["contract_code"], d["curve_position"])}
    assert by_code["CLQ26"] == 1
    assert by_code["CLU26"] == 2
    assert by_code["CLV26"] == 3


def test_silver_futures_pass_status():
    bronze = _make_futures_bronze(_GOOD_FUTURES)
    silver = silver_futures.normalize(bronze, AS_OF)
    statuses = silver.column("silver_status").to_pylist()
    assert all(s in ("PASS", "WARN") for s in statuses)


def test_silver_futures_fail_on_bad_settlement():
    bad = [{"contract_code": "CLQ26", "delivery_month": "2026-08", "settlement": -1.0, "volume": 100}]
    bronze = _make_futures_bronze(bad)
    silver = silver_futures.normalize(bronze, AS_OF)
    assert silver.column("silver_status").to_pylist()[0] == "FAIL"


def test_silver_futures_fail_on_unparseable_code():
    bad = [{"contract_code": "INVALID", "delivery_month": "2026-08", "settlement": 72.0, "volume": 100}]
    bronze = _make_futures_bronze(bad)
    silver = silver_futures.normalize(bronze, AS_OF)
    assert silver.column("silver_status").to_pylist()[0] == "FAIL"


# ──────────────────────────────────────────────────────────
# Silver options
# ──────────────────────────────────────────────────────────

def _good_options(n_strikes=6):
    opts = []
    for i in range(n_strikes):
        strike = 70.0 + i * 2.5
        for cp in ("C", "P"):
            opts.append({
                "option_expiry": "2026-07-18",
                "strike": strike,
                "call_put": cp,
                "settlement": max(0.01, (80.0 - strike) if cp == "C" else (strike - 70.0)),
                "implied_volatility": 0.32,
                "delta": 0.5,
            })
    return opts


def test_silver_options_schema():
    bronze = _make_options_bronze(_good_options())
    silver = silver_options.normalize(bronze, AS_OF)
    for f in ("silver_status", "silver_note"):
        assert f in silver.schema.names


def test_silver_options_pass_with_enough_strikes():
    bronze = _make_options_bronze(_good_options(n_strikes=6))
    silver = silver_options.normalize(bronze, AS_OF)
    statuses = set(silver.column("silver_status").to_pylist())
    assert "FAIL" not in statuses


def test_silver_options_warn_with_few_strikes():
    opts = []
    for cp in ("C", "P"):
        opts.append({
            "option_expiry": "2026-07-18", "strike": 75.0, "call_put": cp,
            "settlement": 1.0, "implied_volatility": 0.3,
        })
    bronze = _make_options_bronze(opts)
    silver = silver_options.normalize(bronze, AS_OF)
    statuses = set(silver.column("silver_status").to_pylist())
    # 1 strike per side → critically sparse → FAIL
    assert "FAIL" in statuses


def test_silver_options_fail_expired():
    opts = [{
        "option_expiry": "2026-06-20",  # before AS_OF
        "strike": 75.0, "call_put": "C", "settlement": 1.0,
    }]
    bronze = _make_options_bronze(opts)
    silver = silver_options.normalize(bronze, AS_OF)
    assert silver.column("silver_status").to_pylist()[0] == "FAIL"


def test_silver_options_fail_negative_settlement():
    opts = [{
        "option_expiry": "2026-07-18", "strike": 75.0, "call_put": "C", "settlement": -0.5,
    }]
    bronze = _make_options_bronze(opts)
    silver = silver_options.normalize(bronze, AS_OF)
    assert silver.column("silver_status").to_pylist()[0] == "FAIL"

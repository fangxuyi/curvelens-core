"""Tests for bronze-layer parsers."""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

from ccvm.parsers import bronze_futures, bronze_options, bronze_eia

SHA = "abc123deadbeef"

# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────

@pytest.fixture
def futures_raw(tmp_path) -> Path:
    data = {
        "source": "yfinance_wti_futures",
        "trade_date": "2026-06-24",
        "fetched_at": "2026-06-24T22:00:00Z",
        "contract_count": 3,
        "settlements": [
            {"trade_date": "2026-06-24", "exchange": "NYMEX", "product": "CL",
             "contract_code": "CLQ26", "delivery_month": "2026-08",
             "settlement": 72.45, "volume": 12000, "open_interest": None,
             "currency": "USD", "price_unit": "USD/BBL", "source_id": "yfinance_wti_futures"},
            {"trade_date": "2026-06-24", "exchange": "NYMEX", "product": "CL",
             "contract_code": "CLU26", "delivery_month": "2026-09",
             "settlement": 71.80, "volume": 5000, "open_interest": None,
             "currency": "USD", "price_unit": "USD/BBL", "source_id": "yfinance_wti_futures"},
            {"trade_date": "2026-06-24", "exchange": "NYMEX", "product": "CL",
             "contract_code": "CLV26", "delivery_month": "2026-10",
             "settlement": 71.20, "volume": 1000, "open_interest": None,
             "currency": "USD", "price_unit": "USD/BBL", "source_id": "yfinance_wti_futures"},
        ],
    }
    p = tmp_path / "yf_cl_futures_20260624.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def options_raw(tmp_path) -> Path:
    data = {
        "source": "etrade_uso_options",
        "underlying": "USO",
        "trade_date": "2026-06-24",
        "settlements": [
            {"trade_date": "2026-06-24", "option_expiry": "2026-07-18",
             "option_symbol": "USO260718C00075000", "underlying_contract": "USO",
             "underlying_delivery_month": "2026-07", "strike": 75.0, "call_put": "C",
             "settlement": 2.15, "bid": 2.10, "ask": 2.20, "volume": 500,
             "open_interest": 3000, "implied_volatility": 0.32, "delta": 0.52,
             "gamma": 0.04, "theta": -0.03, "vega": 0.12,
             "exercise_style": "American", "settlement_style": "Equity_ETF",
             "contract_multiplier": 100, "source_id": "etrade_uso_options",
             "price_note": "USO_proxy"},
            {"trade_date": "2026-06-24", "option_expiry": "2026-07-18",
             "option_symbol": "USO260718P00075000", "underlying_contract": "USO",
             "underlying_delivery_month": "2026-07", "strike": 75.0, "call_put": "P",
             "settlement": 0.85, "bid": 0.80, "ask": 0.90, "volume": 300,
             "open_interest": 2000, "implied_volatility": 0.34, "delta": -0.48,
             "gamma": 0.04, "theta": -0.03, "vega": 0.12,
             "exercise_style": "American", "settlement_style": "Equity_ETF",
             "contract_multiplier": 100, "source_id": "etrade_uso_options",
             "price_note": "USO_proxy"},
        ],
    }
    p = tmp_path / "etrade_uso_options_20260624.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def eia_raw(tmp_path) -> Path:
    data = {
        "response": {
            "total": "3",
            "data": [
                {"period": "2026-06-20", "series": "WCRSTUS1",
                 "series-description": "U.S. Ending Stocks of Crude Oil (Thousand Barrels)",
                 "value": "428000", "units": "MBBL", "duoarea": "NUS", "product": "EPC0"},
                {"period": "2026-06-13", "series": "WCRSTUS1",
                 "series-description": "U.S. Ending Stocks of Crude Oil (Thousand Barrels)",
                 "value": "431000", "units": "MBBL", "duoarea": "NUS", "product": "EPC0"},
            ],
        }
    }
    p = tmp_path / "eia_crude_stocks_20260624.json"
    p.write_text(json.dumps(data))
    return p


# ──────────────────────────────────────────────────────────
# Futures
# ──────────────────────────────────────────────────────────

def test_bronze_futures_row_count(futures_raw):
    t = bronze_futures.parse(futures_raw, SHA)
    assert len(t) == 3


def test_bronze_futures_schema(futures_raw):
    t = bronze_futures.parse(futures_raw, SHA)
    assert "contract_code" in t.schema.names
    assert "delivery_month" in t.schema.names
    assert "settlement" in t.schema.names
    assert "raw_file_sha256" in t.schema.names


def test_bronze_futures_sha256_propagated(futures_raw):
    t = bronze_futures.parse(futures_raw, SHA)
    assert all(v == SHA for v in t.column("raw_file_sha256").to_pylist())


def test_bronze_futures_values(futures_raw):
    t = bronze_futures.parse(futures_raw, SHA)
    codes = t.column("contract_code").to_pylist()
    assert "CLQ26" in codes
    settlements = t.column("settlement").to_pylist()
    assert 72.45 in settlements


# ──────────────────────────────────────────────────────────
# Options
# ──────────────────────────────────────────────────────────

def test_bronze_options_row_count(options_raw):
    t = bronze_options.parse(options_raw, SHA)
    assert len(t) == 2


def test_bronze_options_schema(options_raw):
    t = bronze_options.parse(options_raw, SHA)
    for field in ("trade_date", "option_expiry", "strike", "call_put", "settlement",
                  "implied_volatility", "delta", "source_id", "raw_file_sha256"):
        assert field in t.schema.names


def test_bronze_options_call_put(options_raw):
    t = bronze_options.parse(options_raw, SHA)
    cp = set(t.column("call_put").to_pylist())
    assert cp == {"C", "P"}


# ──────────────────────────────────────────────────────────
# EIA
# ──────────────────────────────────────────────────────────

def test_bronze_eia_row_count(eia_raw):
    t = bronze_eia.parse(eia_raw, SHA)
    assert len(t) == 2


def test_bronze_eia_schema(eia_raw):
    t = bronze_eia.parse(eia_raw, SHA)
    for field in ("period", "series_id", "value", "units", "geography"):
        assert field in t.schema.names


def test_bronze_eia_values(eia_raw):
    t = bronze_eia.parse(eia_raw, SHA)
    values = t.column("value").to_pylist()
    assert 428000.0 in values

"""Tests for ETradeOptionsCollector — all tests use synthetic fixtures, no live API calls."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ccvm.collectors.etrade_options import ETradeOptionsCollector, _oauth1_auth_header
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore

AS_OF = date(2026, 6, 24)

# Minimal E*TRADE OptionChainResponse for USO July 2026 expiry
_FIXTURE_CHAIN = {
    "OptionChainResponse": {
        "SelectedED": {"year": 2026, "month": 7, "day": 18, "expiryType": "MONTHLY"},
        "timeStamp": 1750000000,
        "OptionPair": [
            {
                "Call": {
                    "optionSymbol": "USO260718C00075000",
                    "strikePrice": 75.0,
                    "bid": 2.10, "ask": 2.20, "lastPrice": 2.15,
                    "volume": 500, "openInterest": 3000,
                    "impliedVolatility": 0.32,
                    "OptionGreeks": {"delta": 0.52, "gamma": 0.04, "theta": -0.03, "vega": 0.12},
                },
                "Put": {
                    "optionSymbol": "USO260718P00075000",
                    "strikePrice": 75.0,
                    "bid": 0.80, "ask": 0.90, "lastPrice": 0.85,
                    "volume": 300, "openInterest": 2000,
                    "impliedVolatility": 0.34,
                    "OptionGreeks": {"delta": -0.48, "gamma": 0.04, "theta": -0.03, "vega": 0.12},
                },
            },
            {
                "Call": {
                    "optionSymbol": "USO260718C00080000",
                    "strikePrice": 80.0,
                    "bid": 0.50, "ask": 0.60, "lastPrice": 0.55,
                    "volume": 200, "openInterest": 1500,
                    "impliedVolatility": 0.30,
                    "OptionGreeks": {"delta": 0.22, "gamma": 0.02, "theta": -0.02, "vega": 0.09},
                },
                "Put": {
                    "optionSymbol": "USO260718P00080000",
                    "strikePrice": 80.0,
                    "bid": 4.80, "ask": 5.00, "lastPrice": 4.90,
                    "volume": 150, "openInterest": 800,
                    "impliedVolatility": 0.35,
                    "OptionGreeks": {"delta": -0.78, "gamma": 0.02, "theta": -0.02, "vega": 0.09},
                },
            },
        ],
    }
}

_EXPIRED_CHAIN = {
    "OptionChainResponse": {
        "SelectedED": {"year": 2026, "month": 6, "day": 19, "expiryType": "MONTHLY"},
        "OptionPair": [
            {
                "Call": {"optionSymbol": "USO260619C00075000", "strikePrice": 75.0,
                         "bid": 0.0, "ask": 0.0, "lastPrice": 0.0,
                         "OptionGreeks": {}},
                "Put": {"optionSymbol": "USO260619P00075000", "strikePrice": 75.0,
                        "bid": 0.0, "ask": 0.0, "lastPrice": 0.0,
                        "OptionGreeks": {}},
            }
        ],
    }
}


@pytest.fixture
def collector(tmp_path):
    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    c = ETradeOptionsCollector(raw_store, manifest_db, max_expiries=2)
    # inject dummy credentials so collect() proceeds
    c._tokens = {
        "consumer_key": "TEST_CK",
        "consumer_secret": "TEST_CS",
        "access_token": "TEST_AT",
        "access_token_secret": "TEST_ATS",
    }
    return c


# --- unit: OAuth header ---

def test_oauth1_header_produces_non_empty_string():
    header = _oauth1_auth_header(
        url="https://api.etrade.com/v1/market/optionchains",
        method="GET",
        consumer_key="CK",
        consumer_secret="CS",
        token="AT",
        token_secret="ATS",
        query_params={"symbol": "USO"},
    )
    assert header.startswith("OAuth ")
    assert "oauth_signature=" in header
    assert "oauth_consumer_key=" in header


def test_oauth1_header_different_nonce_each_call():
    kwargs = dict(url="https://api.etrade.com/v1/test", method="GET",
                  consumer_key="K", consumer_secret="S", token="T", token_secret="TS")
    h1 = _oauth1_auth_header(**kwargs)
    h2 = _oauth1_auth_header(**kwargs)
    # nonces differ → signatures differ
    assert h1 != h2


# --- unit: parse_chain ---

def test_parse_chain_returns_correct_count(collector):
    records = collector.parse_chain(_FIXTURE_CHAIN, AS_OF)
    assert len(records) == 4  # 2 strikes × 2 sides


def test_parse_chain_fields_present(collector):
    records = collector.parse_chain(_FIXTURE_CHAIN, AS_OF)
    r = records[0]
    assert r["underlying_contract"] == "USO"
    assert r["trade_date"] == "2026-06-24"
    assert r["option_expiry"] == "2026-07-18"
    assert r["call_put"] in ("C", "P")
    assert r["strike"] > 0
    assert r["settlement"] > 0
    assert r["implied_volatility"] is not None
    assert r["delta"] is not None
    assert r["source_id"] == "etrade_uso_options"
    assert "USO_equity_option_proxy" in r["price_note"]


def test_parse_chain_splits_calls_and_puts(collector):
    records = collector.parse_chain(_FIXTURE_CHAIN, AS_OF)
    calls = [r for r in records if r["call_put"] == "C"]
    puts = [r for r in records if r["call_put"] == "P"]
    assert len(calls) == 2
    assert len(puts) == 2


def test_parse_chain_skips_expired_expiry(collector):
    records = collector.parse_chain(_EXPIRED_CHAIN, AS_OF)
    assert records == []


def test_parse_chain_uses_mid_when_no_last(collector):
    chain = {
        "OptionChainResponse": {
            "SelectedED": {"year": 2026, "month": 8, "day": 21, "expiryType": "MONTHLY"},
            "OptionPair": [
                {
                    "Call": {"optionSymbol": "X", "strikePrice": 75.0,
                             "bid": 2.0, "ask": 3.0, "lastPrice": 0.0,
                             "OptionGreeks": {}},
                    "Put": None,
                }
            ],
        }
    }
    records = collector.parse_chain(chain, AS_OF)
    assert len(records) == 1
    assert records[0]["settlement"] == pytest.approx(2.5)


# --- integration: collect with mocked HTTP ---

def test_collect_skips_when_no_credentials(tmp_path):
    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    c = ETradeOptionsCollector(raw_store, manifest_db)
    c._tokens = {"consumer_key": "", "consumer_secret": "",
                 "access_token": "", "access_token_secret": ""}
    result = c.collect(AS_OF)
    assert result["status"] == "skipped"


def test_collect_writes_raw_and_manifest(collector):
    with patch.object(collector, "_fetch_option_chain", return_value=_FIXTURE_CHAIN):
        result = collector.collect(AS_OF)

    assert result["status"] == "success"
    assert result["records"] > 0
    entries = collector.manifest_db.get_manifest_entries(collector.source_id)
    assert len(entries) == 1
    assert entries[0]["source_id"] == "etrade_uso_options"


def test_collect_idempotent(collector):
    with patch.object(collector, "_fetch_option_chain", return_value=_FIXTURE_CHAIN):
        r1 = collector.collect(AS_OF)
        r2 = collector.collect(AS_OF)

    assert r1["status"] == "success"
    assert r2["status"] == "success"
    assert r2["skipped"] == 1
    entries = collector.manifest_db.get_manifest_entries(collector.source_id)
    assert len(entries) == 1


def test_collect_raw_file_is_valid_json(collector):
    with patch.object(collector, "_fetch_option_chain", return_value=_FIXTURE_CHAIN):
        collector.collect(AS_OF)

    entries = collector.manifest_db.get_manifest_entries(collector.source_id)
    raw_path = entries[0]["raw_path"]
    data = json.loads(Path(raw_path).read_bytes())
    assert data["source"] == "etrade_uso_options"
    assert data["underlying"] == "USO"
    assert isinstance(data["settlements"], list)
    assert len(data["settlements"]) > 0


def test_collect_records_run_in_history(collector):
    with patch.object(collector, "_fetch_option_chain", return_value=_FIXTURE_CHAIN):
        collector.collect(AS_OF)

    runs = collector.manifest_db.get_run_history(collector.source_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "success"

"""Tests for BarchartOptionsCollector — all tests use recorded fixtures, no live API calls."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ccvm.collectors.barchart_options import BarchartOptionsCollector, _active_cl_symbols
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "barchart"
AS_OF = date(2026, 6, 24)


@pytest.fixture
def collector(tmp_path):
    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    c = BarchartOptionsCollector(raw_store, manifest_db)
    c.api_key = "TEST_KEY"  # bypass the env-var check
    return c


@pytest.fixture
def good_fixture_bytes():
    return (FIXTURE_DIR / "barchart_clq26_options_20260624.json").read_bytes()


# --- unit: helpers ---

def test_active_cl_symbols_returns_correct_count():
    syms = _active_cl_symbols(AS_OF, count=5)
    assert len(syms) == 5


def test_active_cl_symbols_start_next_month():
    syms = _active_cl_symbols(AS_OF, count=3)
    # as_of = 2026-06-24, first active contract should be CLQ26 (Aug 2026)
    codes = [s for s, _ in syms]
    assert "CLQ26" in codes


# --- unit: parse_chain ---

def test_parse_chain_good_fixture(collector, good_fixture_bytes):
    records = collector.parse_chain(good_fixture_bytes, AS_OF, "CLQ26", "2026-08")
    assert len(records) == 13  # 7 calls + 6 puts in fixture (strike 74 put but not 76 put... wait)
    # actually fixture has 7 calls + 6 puts = 13
    calls = [r for r in records if r["call_put"] == "C"]
    puts = [r for r in records if r["call_put"] == "P"]
    assert len(calls) == 7
    assert len(puts) == 6


def test_parse_chain_fields_present(collector, good_fixture_bytes):
    records = collector.parse_chain(good_fixture_bytes, AS_OF, "CLQ26", "2026-08")
    r = records[0]
    assert r["underlying_contract"] == "CLQ26"
    assert r["underlying_delivery_month"] == "2026-08"
    assert r["trade_date"] == "2026-06-24"
    assert r["option_expiry"] == "2026-07-17"
    assert r["call_put"] in ("C", "P")
    assert r["strike"] > 0
    assert r["settlement"] >= 0
    assert r["implied_volatility"] is not None
    assert r["delta"] is not None
    assert r["source_id"] == "barchart_wti_options"


def test_parse_chain_skips_expired_options(collector):
    data = {
        "status": {"code": 200, "message": "Success."},
        "results": [
            {"strike": 70.0, "side": "Call", "lastPrice": 1.5,
             "expirationDate": "2026-06-20",  # before AS_OF — should be skipped
             "volume": 100, "openInterest": 500},
            {"strike": 70.0, "side": "Call", "lastPrice": 1.5,
             "expirationDate": "2026-07-17",  # valid
             "volume": 100, "openInterest": 500},
        ]
    }
    records = collector.parse_chain(json.dumps(data).encode(), AS_OF, "CLQ26", "2026-08")
    assert len(records) == 1
    assert records[0]["option_expiry"] == "2026-07-17"


def test_parse_chain_skips_negative_settlement(collector):
    data = {
        "status": {"code": 200, "message": "Success."},
        "results": [
            {"strike": 70.0, "side": "Call", "lastPrice": -1.0,
             "expirationDate": "2026-07-17", "volume": 0, "openInterest": 0},
        ]
    }
    records = collector.parse_chain(json.dumps(data).encode(), AS_OF, "CLQ26", "2026-08")
    assert len(records) == 0


def test_parse_chain_api_error_returns_empty(collector):
    data = {"status": {"code": 400, "message": "Invalid API key."}, "results": None}
    records = collector.parse_chain(json.dumps(data).encode(), AS_OF, "CLQ26", "2026-08")
    assert records == []


# --- integration: collect with mocked HTTP ---

def test_collect_skips_when_no_api_key(tmp_path):
    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    collector = BarchartOptionsCollector(raw_store, manifest_db)
    collector.api_key = ""  # no key
    result = collector.collect(AS_OF)
    assert result["status"] == "skipped"


def test_collect_writes_raw_and_manifest(collector, good_fixture_bytes):
    with patch.object(collector, "_fetch_chain", return_value=good_fixture_bytes):
        result = collector.collect(AS_OF)

    assert result["status"] == "success"
    assert result["records"] > 0
    entries = collector.manifest_db.get_manifest_entries(collector.source_id)
    assert len(entries) == 1
    assert entries[0]["source_id"] == "barchart_wti_options"


def test_collect_idempotent(collector, good_fixture_bytes):
    with patch.object(collector, "_fetch_chain", return_value=good_fixture_bytes):
        r1 = collector.collect(AS_OF)
        r2 = collector.collect(AS_OF)

    assert r1["status"] == "success"
    assert r2["status"] == "success"
    assert r2["skipped"] == 1  # second run skipped due to SHA-256 match
    entries = collector.manifest_db.get_manifest_entries(collector.source_id)
    assert len(entries) == 1  # still only one entry


def test_collect_records_run_in_manifest(collector, good_fixture_bytes):
    with patch.object(collector, "_fetch_chain", return_value=good_fixture_bytes):
        collector.collect(AS_OF)

    runs = collector.manifest_db.get_run_history(collector.source_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "success"

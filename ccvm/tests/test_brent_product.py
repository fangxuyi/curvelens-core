"""ICE Brent profile, calendar, authorized handoff, and knowledge pack."""
from __future__ import annotations

import json
from datetime import date

import pytest

from ccvm.collectors.authorized_market_data import AuthorizedMarketDataCollector
from ccvm.knowledge.loader import knowledge_path, load_calendar
from ccvm.reference import brent_calendar
from ccvm.reference.product import get_product, load_product
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore


def test_brent_profile_uses_authorized_ice_market_data():
    product = load_product("brent")
    assert product.exchange == "ICE Futures Europe"
    assert product.futures_prefix == "B"
    assert product.contract_multiplier == 1000
    assert product.tick_size == pytest.approx(0.01)
    assert product.option_premium_tick_size == pytest.approx(0.01)
    assert product.options_expiry_horizon_months == 12
    assert product.market_data.provider == "authorized_files"
    assert product.market_data.futures_source_url == "https://www.ice.com/report/10"
    assert product.market_data.options_source_url == "https://www.ice.com/report/166"
    assert product.market_data.source_contract == "B"
    assert product.bulletin is None
    assert product.fundamentals_provider == "eia_weekly_petroleum"
    assert product.benchmark.name == "WTI"
    assert product.cot_contract_market_code is None
    assert [role.key for role in product.analysis_roles] == [
        "futures_curve", "vol_surface", "fundamentals",
    ]


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 9, date(2026, 7, 31)),
        (2026, 10, date(2026, 8, 28)),
        (2026, 11, date(2026, 9, 30)),
        (2026, 12, date(2026, 10, 30)),
        (2027, 1, date(2026, 11, 30)),
        (2027, 2, date(2026, 12, 30)),
    ],
)
def test_brent_futures_expiries_match_ice(year, month, expected):
    assert brent_calendar.futures_last_trade_date(year, month) == expected


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 9, date(2026, 7, 28)),
        (2026, 10, date(2026, 8, 25)),
        (2026, 11, date(2026, 9, 25)),
        (2026, 12, date(2026, 10, 27)),
        (2027, 1, date(2026, 11, 25)),
        (2027, 2, date(2026, 12, 23)),
        (2027, 3, date(2027, 1, 26)),
        (2027, 4, date(2027, 2, 23)),
    ],
)
def test_brent_option_expiries_match_ice(year, month, expected):
    assert brent_calendar.option_expiry_date(year, month) == expected


def test_brent_active_curve_starts_with_first_unexpired_contract():
    contracts = brent_calendar.active_contracts(date(2026, 7, 23), 4)
    assert [item.contract_code for item in contracts] == [
        "BU26", "BV26", "BX26", "BZ26",
    ]


def test_authorized_handoff_validates_and_persists_both_files(monkeypatch, tmp_path):
    monkeypatch.setenv("CCVM_PRODUCT", "brent")
    get_product.cache_clear()
    data_dir = tmp_path / "data"
    input_dir = (
        data_dir / "authorized_market_data" / "trade_date=2026-07-23"
    )
    input_dir.mkdir(parents=True)
    futures = {
        "trade_date": "2026-07-23",
        "exchange": "ICE Futures Europe",
        "product": "B",
        "settlements": [{
            "contract_code": "BU26", "delivery_month": "2026-09",
            "settlement": 82.15, "volume": 1000, "open_interest": 2000,
        }],
    }
    options = {
        "trade_date": "2026-07-23",
        "exchange": "ICE Futures Europe",
        "product": "B",
        "settlements": [{
            "option_expiry": "2026-07-28",
            "underlying_contract": "BU26",
            "underlying_delivery_month": "2026-09",
            "strike": 82.0, "call_put": "C", "settlement": 1.25,
        }],
    }
    (input_dir / "futures.json").write_text(json.dumps(futures))
    (input_dir / "options.json").write_text(json.dumps(options))

    database = ManifestDB(data_dir / "manifests" / "manifest.duckdb")
    result = AuthorizedMarketDataCollector(
        data_dir, RawStore(data_dir), database,
    ).collect(date(2026, 7, 23))

    assert result["status"] == "success"
    assert result["success"] == 2
    assert {entry["source_id"] for entry in database.get_manifest_entries()} == {
        "authorized_brent_futures", "authorized_brent_options",
    }
    get_product.cache_clear()


def test_authorized_handoff_rejects_wrong_trade_date(monkeypatch, tmp_path):
    monkeypatch.setenv("CCVM_PRODUCT", "brent")
    get_product.cache_clear()
    data_dir = tmp_path / "data"
    input_dir = (
        data_dir / "authorized_market_data" / "trade_date=2026-07-23"
    )
    input_dir.mkdir(parents=True)
    wrong = {
        "trade_date": "2026-07-22",
        "exchange": "ICE Futures Europe",
        "product": "B",
        "settlements": [{
            "contract_code": "BU26", "delivery_month": "2026-09",
            "settlement": 82.15,
        }],
    }
    (input_dir / "futures.json").write_text(json.dumps(wrong))
    (input_dir / "options.json").write_text(json.dumps(wrong))
    database = ManifestDB(data_dir / "manifests" / "manifest.duckdb")

    result = AuthorizedMarketDataCollector(
        data_dir, RawStore(data_dir), database,
    ).collect(date(2026, 7, 23))

    assert result["status"] == "failed"
    assert "does not match" in result["detail"]
    get_product.cache_clear()


def test_brent_knowledge_pack_is_complete():
    pack = knowledge_path("brent")
    assert load_calendar("brent").get("product") == "brent"
    for filename in (
        "conventions.md", "regimes.md", "seasonality.md", "analogs.md",
        "macro.md", "fundamentals.md",
    ):
        text = (pack / filename).read_text()
        assert "*Last reviewed: 2026-07-23" in text[:260]

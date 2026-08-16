"""Shared equity-index profiles, context capability, and calendars."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ccvm.collectors.equity_context import (
    EquityContextCollector, load_equity_context,
)
from ccvm.collectors.authorized_market_data import AuthorizedMarketDataCollector
from ccvm.knowledge.loader import knowledge_path, load_calendar
from ccvm.reference import equity_index_calendar
from ccvm.reference.product import get_product, load_product
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore


@pytest.mark.parametrize(
    "key,prefix,proxy,multiplier",
    [
        ("sp500", "ES", "SPY", 50),
        ("nasdaq100", "NQ", "QQQ", 20),
        ("russell2000", "RTY", "IWM", 50),
    ],
)
def test_equity_index_profiles(key, prefix, proxy, multiplier):
    product = load_product(key)
    assert product.market_data.provider == "authorized_files"
    assert product.futures_prefix == prefix
    assert product.contract_multiplier == multiplier
    assert product.listed_futures_months == (3, 6, 9, 12)
    assert product.options_expiry_horizon_months == 12
    assert product.equity_context.proxy_ticker == proxy
    assert len(product.equity_context.sector_proxies) == 11
    assert {role.key for role in product.analysis_roles} >= {
        "index_market", "vol_surface", "sectors_corporate",
    }


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 9, date(2026, 9, 18)),
        (2026, 12, date(2026, 12, 18)),
        (2027, 3, date(2027, 3, 19)),
    ],
)
def test_quarterly_equity_expiries(year, month, expected):
    assert equity_index_calendar.futures_last_trade_date(year, month) == expected
    assert equity_index_calendar.option_expiry_date(year, month) == expected


def test_active_quarterly_contracts_use_selected_product(monkeypatch):
    monkeypatch.setenv("CCVM_PRODUCT", "nasdaq100")
    get_product.cache_clear()
    contracts = equity_index_calendar.active_contracts(date(2026, 7, 23), 4)
    assert [item.contract_code for item in contracts] == [
        "NQU26", "NQZ26", "NQH27", "NQM27",
    ]
    get_product.cache_clear()


@pytest.mark.parametrize("key", ["sp500", "nasdaq100", "russell2000"])
def test_equity_knowledge_pack_is_complete(key):
    pack = knowledge_path(key)
    assert load_calendar(key)["product"] == key
    for filename in (
        "conventions.md", "macro.md", "fundamentals.md", "regimes.md",
        "seasonality.md", "analogs.md",
    ):
        assert "*Last reviewed: 2026-07-23" in (pack / filename).read_text()[:260]


def test_equity_context_snapshot_is_product_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("CCVM_PRODUCT", "sp500")
    get_product.cache_clear()
    data_dir = tmp_path / "data"
    database = ManifestDB(data_dir / "manifests" / "manifest.duckdb")
    collector = EquityContextCollector(
        RawStore(data_dir), database,
        earnings_api_key="test", sec_user_agent="CurveLens test@example.com",
    )
    monkeypatch.setattr(collector, "_market_context", lambda _: (
        {
            "ticker": "SPY", "name": "SPDR S&P 500 ETF Trust",
            "observation_date": "2026-07-23", "close": 700.0,
            "return_1d": 0.01,
        },
        [
            {
                "ticker": "XLK", "name": "Information Technology",
                "observation_date": "2026-07-23", "return_1d": 0.02,
            }
        ],
    ))
    monkeypatch.setattr(collector, "_earnings", lambda _: [{
        "ticker": "MSFT", "report_date": "2026-07-29",
    }])
    monkeypatch.setattr(collector, "_filings", lambda _: [{
        "ticker": "AAPL", "form": "8-K", "filing_date": "2026-07-23",
    }])

    result = collector.collect(date(2026, 7, 23))

    assert result["status"] == "success"
    snapshot = load_equity_context(data_dir, "sp500", "2026-07-23")
    assert snapshot["index_proxy"]["ticker"] == "SPY"
    assert snapshot["top_sectors"][0]["ticker"] == "XLK"
    assert snapshot["upcoming_earnings"][0]["ticker"] == "MSFT"
    assert snapshot["recent_material_filings"][0]["form"] == "8-K"
    entries = database.get_manifest_entries()
    assert len(entries) == 1
    assert json.loads(Path(entries[0]["raw_path"]).read_text())["product"] == "sp500"
    get_product.cache_clear()


@pytest.mark.parametrize(
    "key,exchange,product,contract,settlement",
    [
        ("sp500", "CME", "ES", "ESU26", 6500.25),
        ("nasdaq100", "CME", "NQ", "NQU26", 24500.25),
        ("russell2000", "CME", "RTY", "RTYU26", 2500.1),
    ],
)
def test_authorized_quarterly_handoff(
    monkeypatch, tmp_path, key, exchange, product, contract, settlement,
):
    monkeypatch.setenv("CCVM_PRODUCT", key)
    get_product.cache_clear()
    data_dir = tmp_path / key
    input_dir = (
        data_dir / "authorized_market_data" / "trade_date=2026-07-23"
    )
    input_dir.mkdir(parents=True)
    (input_dir / "futures.json").write_text(json.dumps({
        "trade_date": "2026-07-23", "exchange": exchange, "product": product,
        "settlements": [{
            "contract_code": contract, "delivery_month": "2026-09",
            "settlement": settlement,
        }],
    }))
    (input_dir / "options.json").write_text(json.dumps({
        "trade_date": "2026-07-23", "exchange": exchange, "product": product,
        "settlements": [{
            "option_expiry": "2026-09-18",
            "underlying_contract": contract,
            "underlying_delivery_month": "2026-09",
            "strike": round(settlement), "call_put": "C",
            "settlement": 100.0,
        }],
    }))
    database = ManifestDB(data_dir / "manifests" / "manifest.duckdb")

    result = AuthorizedMarketDataCollector(
        data_dir, RawStore(data_dir), database,
    ).collect(date(2026, 7, 23))

    assert result["status"] == "success"
    assert result["success"] == 2
    get_product.cache_clear()

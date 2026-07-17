"""Gold product profile, serial option mapping, calendar, and runtime isolation."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ccvm.reference import gold_calendar
from ccvm.reference.product import get_product, load_product
from ccvm.runtime import data_dir
from ccvm.knowledge.loader import knowledge_path, load_calendar

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "gold_expiry_calendar.json").read_text()
)


def _cases(section: str):
    return [
        (int(key[:4]), int(key[5:7]), date.fromisoformat(value))
        for key, value in FIXTURE[section].items()
    ]


class TestGoldProfile:
    def test_loads_without_wti_capabilities(self):
        product = load_product("gold")
        assert product.exchange == "COMEX"
        assert product.futures_prefix == "GC"
        assert product.options_prefix == "OG"
        assert product.fundamentals_provider is None
        assert product.benchmark is None
        assert product.cot_contract_market_code == "088691"
        assert product.bulletin.strike_scale == 1
        assert product.bulletin.expiry_basis == "option_month"

    @pytest.mark.parametrize(
        "option_month,underlying_month", [(1, 2), (2, 2), (3, 4), (12, 12)],
    )
    def test_serial_option_month_mapping(self, option_month, underlying_month):
        product = get_product("gold")
        _, contract, delivery_month = product.option_contract_info(2027, option_month)
        assert contract == product.contract_code(2027, underlying_month)
        assert delivery_month == f"2027-{underlying_month:02d}"

    def test_august_option_contract_info(self):
        expiry, contract, delivery_month = get_product("gold").option_contract_info(2026, 8)
        assert expiry == date(2026, 7, 27)
        assert contract == "GCQ26"
        assert delivery_month == "2026-08"


class TestGoldCalendar:
    @pytest.mark.parametrize("year,month,expected", _cases("futures_ltd"))
    def test_futures_ltd(self, year, month, expected):
        assert gold_calendar.futures_last_trade_date(year, month) == expected

    @pytest.mark.parametrize("year,month,expected", _cases("option_expiry"))
    def test_option_expiry(self, year, month, expected):
        assert gold_calendar.option_expiry_for_option_month(year, month) == expected

    def test_preholiday_adjustment(self):
        # Four business days before Dec-2026 month-end is Dec 24; Rule 115101.E
        # moves an expiry immediately preceding Christmas to Dec 23.
        assert gold_calendar.option_expiry_for_option_month(2027, 1) == date(2026, 12, 23)


def test_runtime_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CCVM_DATA_DIR", str(tmp_path / "gold-data"))
    assert data_dir() == (tmp_path / "gold-data").resolve()


def test_gold_knowledge_pack_shape():
    pack = knowledge_path("gold")
    assert load_calendar("gold").get("dated") == []
    for filename in ("conventions.md", "regimes.md", "seasonality.md", "analogs.md"):
        text = (pack / filename).read_text()
        assert "*Last reviewed: 2026-07-16" in text[:240]

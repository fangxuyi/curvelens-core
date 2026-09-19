"""Gold product profile, serial option mapping, calendar, and runtime isolation."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ccvm.reference import gold_calendar
from ccvm.reference.product import available_products, get_product, load_product
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
        assert product.macro.provider == "fred"
        assert {s.series_id for s in product.macro.series} == {
            "DFII10", "DTWEXBGS", "T10YIE", "DGS3MO", "DGS10", "GVZCLS",
        }
        assert product.benchmark is None
        assert product.cot_contract_market_code == "088691"
        assert product.bulletin.strike_scale == 1
        assert product.bulletin.expiry_basis == "option_month"
        assert product.rnd_quality_gate is True

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
        assert expiry == date(2026, 7, 28)
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
        assert gold_calendar.option_expiry_for_option_month(2027, 1) == date(2026, 12, 28)


def test_runtime_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CCVM_DATA_DIR", str(tmp_path / "gold-data"))
    assert data_dir() == (tmp_path / "gold-data").resolve()


def test_runtime_data_dir_defaults_to_product_namespace(monkeypatch):
    monkeypatch.delenv("CCVM_DATA_DIR", raising=False)
    monkeypatch.setenv("CCVM_PRODUCT", "gold")
    assert data_dir().parts[-4:] == ("ccvm", "data", "products", "gold")
    assert data_dir() != Path(__file__).resolve().parents[1] / "data" / "gold"


def test_dashboard_can_resolve_each_configured_product_namespace(monkeypatch):
    monkeypatch.delenv("CCVM_DATA_DIR", raising=False)

    assert {
        "brent", "copper", "corn", "gold", "silver", "wti",
        "sp500", "nasdaq100", "russell2000",
    } <= set(
        available_products()
    )
    assert data_dir("gold").parts[-2:] == ("products", "gold")
    assert data_dir("wti").parts[-2:] == ("products", "wti")
    assert data_dir("corn").parts[-2:] == ("products", "corn")
    assert data_dir("silver").parts[-2:] == ("products", "silver")


def test_single_product_override_rejects_cross_product_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("CCVM_PRODUCT", "gold")
    monkeypatch.setenv("CCVM_DATA_DIR", str(tmp_path / "gold-data"))

    with pytest.raises(ValueError, match="single-product override"):
        data_dir("wti")


def test_runtime_data_dir_rejects_unsafe_product(monkeypatch):
    monkeypatch.delenv("CCVM_DATA_DIR", raising=False)
    monkeypatch.setenv("CCVM_PRODUCT", "../gold")
    with pytest.raises(ValueError, match="Invalid CCVM_PRODUCT"):
        data_dir()


def test_gold_knowledge_pack_shape():
    pack = knowledge_path("gold")
    assert load_calendar("gold").get("dated") == []
    for filename in ("conventions.md", "regimes.md", "seasonality.md", "analogs.md",
                     "macro.md"):
        text = (pack / filename).read_text()
        assert "*Last reviewed: 2026-07-" in text[:260]

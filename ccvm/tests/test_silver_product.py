"""Silver product profile, calendar, bulletin, roles, and knowledge pack."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ccvm.collectors import cme_bulletin_pdf, yfinance_futures
from ccvm.knowledge.loader import knowledge_path, load_calendar
from ccvm.reference import silver_calendar
from ccvm.reference.product import get_product, load_product

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "silver_expiry_calendar.json").read_text()
)


def _cases(section: str):
    return [
        (int(key[:4]), int(key[5:7]), date.fromisoformat(value))
        for key, value in FIXTURE[section].items()
    ]


def _row(strike: int, settlement: str) -> str:
    return (f"{strike} ---- ---- ---- ---- ---- {settlement} UNCH .5000 "
            "---- ---- 10 ---- 100 UNCH")


class TestSilverProfile:
    def test_profile_has_hybrid_analysis_capabilities(self):
        product = load_product("silver")
        assert product.exchange == "COMEX"
        assert product.futures_prefix == "SI"
        assert product.options_prefix == "SO"
        assert product.contract_multiplier == 5000
        assert product.tick_size == 0.005
        assert product.option_premium_tick_size == 0.001
        assert product.listed_futures_months == tuple(range(1, 13))
        assert product.fundamentals_provider is None
        assert product.macro.provider == "fred"
        assert {s.series_id for s in product.macro.series} == {
            "DFII10", "DTWEXBGS", "T10YIE", "DGS3MO", "DGS10",
            "IPG3344S", "PCOPPUSDM", "IPN221114T8SQ",
        }
        assert product.cot_contract_market_code == "084691"
        assert product.bulletin.strike_scale == 100
        assert product.bulletin.expiry_basis == "option_month"
        assert [role.key for role in product.analysis_roles] == [
            "futures_curve", "vol_surface", "macro_fundamentals",
        ]

    @pytest.mark.parametrize(
        "option_month,underlying_month",
        [(1, 3), (2, 3), (3, 3), (4, 5), (10, 12), (12, 12)],
    )
    def test_serial_option_month_mapping(self, option_month, underlying_month):
        product = get_product("silver")
        _, contract, delivery_month = product.option_contract_info(2027, option_month)
        assert contract == product.contract_code(2027, underlying_month)
        assert delivery_month == f"2027-{underlying_month:02d}"

    def test_august_option_maps_to_september_future(self):
        expiry, contract, delivery_month = get_product("silver").option_contract_info(2026, 8)
        assert expiry == date(2026, 7, 28)
        assert contract == "SIU26"
        assert delivery_month == "2026-09"


class TestSilverCalendar:
    @pytest.mark.parametrize("year,month,expected", _cases("futures_ltd"))
    def test_futures_ltd(self, year, month, expected):
        assert silver_calendar.futures_last_trade_date(year, month) == expected

    @pytest.mark.parametrize("year,month,expected", _cases("option_expiry"))
    def test_option_expiry(self, year, month, expected):
        assert silver_calendar.option_expiry_for_option_month(year, month) == expected

    def test_preholiday_adjustment(self):
        assert silver_calendar.option_expiry_for_option_month(2027, 1) == date(2026, 12, 28)

    def test_exchange_listing_pattern(self):
        contracts = silver_calendar.active_contracts(date(2026, 7, 23), 6)
        assert [c.contract_code for c in contracts] == [
            "SIN26", "SIQ26", "SIU26", "SIV26", "SIX26", "SIZ26",
        ]


def test_yfinance_listing_uses_current_consecutive_month_cycle(monkeypatch):
    monkeypatch.setattr(yfinance_futures, "get_product", lambda: get_product("silver"))
    contracts = yfinance_futures._active_contracts(date(2026, 7, 23), 6)
    assert [contract for _, contract, _ in contracts] == [
        "SIN26", "SIQ26", "SIU26", "SIV26", "SIX26", "SIZ26",
    ]


def test_monthly_section64_rows_are_scaled_mapped_and_isolated(monkeypatch, tmp_path):
    text = "\n".join([
        "SO CALL COMEX SILVER OPTIONS", "AUG26", _row(7000, "2.345"), "TOTAL",
        "SO1 CALL COMEX SILVER WEEKLY OPTIONS", "AUG26",
        _row(7000, "9.999"), "TOTAL",
        "SO PUT COMEX SILVER OPTIONS", "AUG26", _row(7000, "1.125"), "TOTAL",
    ])
    monkeypatch.setattr(cme_bulletin_pdf, "_pdftotext", lambda _path: text)
    monkeypatch.setattr(cme_bulletin_pdf, "get_product", lambda: get_product("silver"))
    records = cme_bulletin_pdf.parse(tmp_path / "unused.pdf", date(2026, 7, 23))

    assert [(r["call_put"], r["strike"], r["settlement"]) for r in records] == [
        ("C", 70.0, 2.345), ("P", 70.0, 1.125),
    ]
    assert {r["option_expiry"] for r in records} == {"2026-07-28"}
    assert {r["underlying_contract"] for r in records} == {"SIU26"}
    assert {r["contract_multiplier"] for r in records} == {5000}


def test_silver_news_and_knowledge_are_product_specific():
    product = load_product("silver")
    assert {"silver_institute", "energy_department"} <= {
        source[0] for source in product.news.sources
    }
    assert {"solar", "photovoltaic", "semiconductor", "recycling"} <= set(
        product.news.keywords
    )

    pack = knowledge_path("silver")
    assert load_calendar("silver").get("dated") == []
    for filename in (
        "conventions.md", "regimes.md", "seasonality.md", "analogs.md",
        "macro.md", "fundamentals.md",
    ):
        text = (pack / filename).read_text()
        assert "*Last reviewed: 2026-07-23" in text[:260]

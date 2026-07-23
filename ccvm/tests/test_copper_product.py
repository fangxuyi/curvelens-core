"""Copper profile, contract calendar, bulletin isolation, and knowledge pack."""
from __future__ import annotations

from datetime import date

import pytest

from ccvm.collectors import cme_bulletin_pdf, yfinance_futures
from ccvm.knowledge.loader import knowledge_path, load_calendar
from ccvm.reference import copper_calendar
from ccvm.reference.product import get_product, load_product


def _row(strike: int, settlement: str) -> str:
    return (f"{strike} ---- ---- ---- ---- ---- {settlement} UNCH .5000 "
            "---- ---- 10 ---- 100 UNCH")


def test_copper_profile_and_capabilities():
    product = load_product("copper")
    assert product.exchange == "COMEX"
    assert product.futures_prefix == "HG"
    assert product.options_prefix == "HX"
    assert product.contract_multiplier == 25_000
    assert product.tick_size == pytest.approx(0.0005)
    assert product.option_premium_tick_size == pytest.approx(0.0005)
    assert product.options_expiry_horizon_months == 12
    assert product.cot_contract_market_code == "085692"
    assert product.bulletin.strike_scale == 100
    assert product.bulletin.expiry_basis == "option_month"
    assert [role.key for role in product.analysis_roles] == [
        "futures_curve", "vol_surface", "macro_fundamentals",
    ]


@pytest.mark.parametrize(
    "option_month,underlying_month",
    [(1, 3), (3, 3), (4, 5), (8, 9), (10, 12), (12, 12)],
)
def test_copper_serial_option_mapping(option_month, underlying_month):
    _, contract, delivery = get_product("copper").option_contract_info(
        2027, option_month,
    )
    assert contract == get_product("copper").contract_code(2027, underlying_month)
    assert delivery == f"2027-{underlying_month:02d}"


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 7, date(2026, 7, 29)),
        (2026, 8, date(2026, 8, 27)),
        (2026, 12, date(2026, 12, 29)),
    ],
)
def test_copper_futures_last_trade_dates(year, month, expected):
    assert copper_calendar.futures_last_trade_date(year, month) == expected


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 8, date(2026, 7, 28)),
        (2026, 9, date(2026, 8, 26)),
        (2027, 1, date(2026, 12, 28)),
    ],
)
def test_copper_option_expiry_dates(year, month, expected):
    assert copper_calendar.option_expiry_for_option_month(year, month) == expected


def test_yfinance_uses_hg_consecutive_contracts(monkeypatch):
    monkeypatch.setattr(yfinance_futures, "get_product", lambda: get_product("copper"))
    contracts = yfinance_futures._active_contracts(date(2026, 7, 23), 4)
    assert [code for _, code, _ in contracts] == [
        "HGN26", "HGQ26", "HGU26", "HGV26",
    ]


def test_section64_selects_monthly_hx_and_excludes_weeklies(monkeypatch, tmp_path):
    text = "\n".join([
        "HX CALL COMEX COPPER OPTIONS", "AUG26", _row(600, "0.1250"), "TOTAL",
        "H1E CALL COPPER WEEKLY OPTIONS", "AUG26", _row(600, "9.9990"), "TOTAL",
        "HX PUT COMEX COPPER OPTIONS", "AUG26", _row(600, "0.0750"), "TOTAL",
    ])
    monkeypatch.setattr(cme_bulletin_pdf, "_pdftotext", lambda _path: text)
    monkeypatch.setattr(cme_bulletin_pdf, "get_product", lambda: get_product("copper"))
    records = cme_bulletin_pdf.parse(tmp_path / "unused.pdf", date(2026, 7, 23))

    assert [(r["call_put"], r["strike"], r["settlement"]) for r in records] == [
        ("C", 6.0, 0.125), ("P", 6.0, 0.075),
    ]
    assert {r["option_expiry"] for r in records} == {"2026-07-28"}
    assert {r["underlying_contract"] for r in records} == {"HGU26"}


def test_copper_knowledge_pack_is_complete():
    pack = knowledge_path("copper")
    assert load_calendar("copper").get("product") == "copper"
    for filename in (
        "conventions.md", "regimes.md", "seasonality.md", "analogs.md",
        "macro.md", "fundamentals.md",
    ):
        text = (pack / filename).read_text()
        assert "*Last reviewed: 2026-07-23" in text[:260]

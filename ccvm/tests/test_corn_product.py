"""Corn profile, calendar, bulletin notation, and crop fundamentals."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from ccvm.analytics import corn_fundamentals
from ccvm.collectors import cme_bulletin_pdf
from ccvm.collectors import yfinance_futures
from ccvm.collectors.usda_nass import USDANASSCornCollector
from ccvm.fundamentals import get_provider
from ccvm.normalizers import silver_usda_nass
from ccvm.parsers import bronze_usda_nass
from ccvm.reference import corn_calendar
from ccvm.reference.product import get_product, load_product
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore


def test_corn_profile_and_capabilities():
    product = load_product("corn")
    assert product.exchange == "CBOT"
    assert product.futures_prefix == "ZC"
    assert product.options_prefix == "OZC"
    assert product.listed_futures_months == (3, 5, 7, 9, 12)
    assert product.futures_price_scale == pytest.approx(0.01)
    assert product.tick_size == pytest.approx(0.0025)
    assert product.option_premium_tick_size == pytest.approx(0.00125)
    assert product.bulletin.premium_format == "grain_eighth_cents_to_dollars"
    assert product.fundamentals_provider == "usda_nass_corn"
    assert product.cot_contract_market_code == "002602"
    assert [role.key for role in product.analysis_roles] == [
        "futures_curve", "vol_surface", "fundamentals",
    ]
    provider = get_provider(product.fundamentals_provider)
    assert provider.collector_cls.__name__ == "USDANASSCornCollector"


@pytest.mark.parametrize(
    "option_month,underlying_month",
    [(1, 3), (3, 3), (4, 5), (8, 9), (10, 12), (12, 12)],
)
def test_serial_option_mapping(option_month, underlying_month):
    _, contract, delivery = get_product("corn").option_contract_info(2027, option_month)
    assert contract == get_product("corn").contract_code(2027, underlying_month)
    assert delivery == f"2027-{underlying_month:02d}"


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 7, date(2026, 7, 14)),
        (2026, 9, date(2026, 9, 14)),
        (2026, 12, date(2026, 12, 14)),
        (2027, 3, date(2027, 3, 12)),
        (2027, 5, date(2027, 5, 14)),
    ],
)
def test_futures_last_trade_dates(year, month, expected):
    assert corn_calendar.futures_last_trade_date(year, month) == expected


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 7, date(2026, 6, 26)),
        (2026, 8, date(2026, 7, 24)),
        (2026, 9, date(2026, 8, 21)),
        (2026, 10, date(2026, 9, 25)),
        (2026, 11, date(2026, 10, 23)),
        (2026, 12, date(2026, 11, 20)),
        (2027, 1, date(2026, 12, 24)),
        (2027, 3, date(2027, 2, 19)),
        (2027, 5, date(2027, 4, 23)),
        (2027, 7, date(2027, 6, 25)),
    ],
)
def test_option_expiry_dates_match_section_56(year, month, expected):
    assert corn_calendar.option_expiry_for_option_month(year, month) == expected


def _bulletin_row(strike: int, settlement: str) -> str:
    return (f"{strike} ---- ---- ---- ---- ---- {settlement} UNCH .5000 "
            "---- ---- 10 ---- 100 UNCH")


def test_corn_bulletin_sections_and_grain_eighths(monkeypatch, tmp_path):
    text = "\n".join([
        "CORN CALL", "AUG26 CORN CALL", _bulletin_row(450, "495"), "TOTAL",
        "OAT CALL", "AUG26", _bulletin_row(450, "999"), "TOTAL",
        "CORN PUT", "AUG26 CORN PUT", _bulletin_row(450, "123"), "TOTAL",
    ])
    monkeypatch.setattr(cme_bulletin_pdf, "_pdftotext", lambda _path: text)
    monkeypatch.setattr(cme_bulletin_pdf, "get_product", lambda: get_product("corn"))
    records = cme_bulletin_pdf.parse(tmp_path / "unused.pdf", date(2026, 7, 20))
    assert [(row["call_put"], row["strike"], row["settlement"]) for row in records] == [
        ("C", 4.5, pytest.approx(0.49625)),
        ("P", 4.5, pytest.approx(0.12375)),
    ]
    assert {row["underlying_contract"] for row in records} == {"ZCU26"}
    assert {row["option_expiry"] for row in records} == {"2026-07-24"}


def test_yfinance_uses_listed_months_and_normalizes_cents(monkeypatch):
    product = get_product("corn")
    monkeypatch.setattr(yfinance_futures, "get_product", lambda: product)
    contracts = yfinance_futures._active_contracts(date(2026, 7, 20), 3)
    assert contracts == [
        ("ZCU26.CBT", "ZCU26", "2026-09"),
        ("ZCZ26.CBT", "ZCZ26", "2026-12"),
        ("ZCH27.CBT", "ZCH27", "2027-03"),
    ]
    frame = pd.DataFrame(
        {"Close": [452.75], "Volume": [1000]},
        index=pd.DatetimeIndex(["2026-07-20"]),
    )
    monkeypatch.setattr(yfinance_futures.yf, "download", lambda *args, **kwargs: frame)
    collector = object.__new__(yfinance_futures.YFinanceFuturesCollector)
    collector.num_months = 1
    collector.source_id = "yfinance_corn_futures"
    rows = collector.fetch_and_parse(date(2026, 7, 20))
    assert rows[0]["contract_code"] == "ZCU26"
    assert rows[0]["settlement"] == pytest.approx(4.5275)


def test_yfinance_keeps_unexpired_current_delivery_month(monkeypatch):
    product = get_product("corn")
    monkeypatch.setattr(yfinance_futures, "get_product", lambda: product)
    assert yfinance_futures._active_contracts(date(2026, 12, 2), 2) == [
        ("ZCZ26.CBT", "ZCZ26", "2026-12"),
        ("ZCH27.CBT", "ZCH27", "2027-03"),
    ]


def test_usda_nass_crop_pipeline(tmp_path):
    observations = []
    for week, good, excellent in (("2026-07-12", "50", "14"),
                                  ("2026-07-19", "47", "13")):
        for rating, value in (("GOOD", good), ("EXCELLENT", excellent)):
            observations.append({
                "year": 2026, "week_ending": week, "Value": value,
                "short_desc": f"CORN - CONDITION, MEASURED IN PCT {rating}",
                "statisticcat_desc": "CONDITION", "unit_desc": "PCT",
                "reference_period_desc": "WEEK",
            })
    observations.extend([
        {"year": 2026, "week_ending": "2026-07-19", "Value": "56",
         "short_desc": "CORN - SILKING, MEASURED IN PCT", "statisticcat_desc": "PROGRESS",
         "unit_desc": "PCT", "reference_period_desc": "WEEK"},
        {"year": 2026, "Value": "181.0", "short_desc": "CORN - YIELD, MEASURED IN BU / ACRE",
         "statisticcat_desc": "YIELD", "unit_desc": "BU / ACRE",
         "reference_period_desc": "YEAR"},
        {"year": 2026, "Value": "15,010,000,000",
         "short_desc": "CORN - PRODUCTION, MEASURED IN BU",
         "statisticcat_desc": "PRODUCTION", "unit_desc": "BU",
         "reference_period_desc": "YEAR"},
    ])
    raw = tmp_path / "nass.json"
    raw.write_text(json.dumps({"data": observations}))
    bronze = bronze_usda_nass.parse(raw, "abc123")
    silver = silver_usda_nass.normalize(bronze, date(2026, 7, 20))
    features = corn_fundamentals.compute(silver, date(2026, 7, 20))
    section = corn_fundamentals.report_section(features)
    assert section["condition_good_excellent_pct"] == pytest.approx(60.0)
    assert section["condition_wow_pp"] == pytest.approx(-4.0)
    assert section["yield_bu_per_acre"] == pytest.approx(181.0)
    assert section["production_million_bushels"] == pytest.approx(15010.0)
    assert section["supply_signal"] == "draw"
    assert section["scenario_trigger"] == "bull_watch"


def test_usda_nass_skips_cleanly_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("USDA_NASS_API_KEY", raising=False)
    database = ManifestDB(tmp_path / "manifest.duckdb")
    result = USDANASSCornCollector(RawStore(tmp_path), database).collect(
        date(2026, 7, 20)
    )
    assert result["status"] == "skipped"
    assert result["detail"] == "USDA_NASS_API_KEY not configured"
    assert database.get_manifest_entry_count() == 0

"""Tests for the daily quality report generator."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

from ccvm.validation.quality_report import generate, futures_section, options_section

AS_OF = date(2026, 6, 24)


def _silver_futures_table(n=5, all_pass=True):
    from ccvm.parsers.bronze_futures import _SCHEMA as BSCHEMA
    from ccvm.normalizers.silver_futures import _SILVER_SCHEMA
    rows = {f.name: [] for f in _SILVER_SCHEMA}
    for i in range(n):
        dm = f"2026-{8+i:02d}"
        rows["trade_date"].append("2026-06-24")
        rows["exchange"].append("NYMEX")
        rows["product"].append("CL")
        rows["contract_code"].append(f"CLQ{26+i}")
        rows["delivery_month"].append(dm)
        rows["settlement"].append(72.0 - i * 0.5)
        rows["volume"].append(10000 - i * 1000)
        rows["open_interest"].append(None)
        rows["currency"].append("USD")
        rows["price_unit"].append("USD/BBL")
        rows["last_trade_date"].append("2026-07-21")
        rows["cl_option_expiry"].append("2026-07-13")
        rows["days_to_expiry"].append(27 - i * 30)
        rows["curve_position"].append(i + 1)
        rows["source_id"].append("yfinance_wti_futures")
        rows["raw_file_sha256"].append("abc")
        rows["silver_status"].append("PASS" if all_pass else ("FAIL" if i == 0 else "PASS"))
        rows["silver_note"].append("")
    return pa.table(rows, schema=_SILVER_SCHEMA)


def _silver_options_table(n=20):
    from ccvm.normalizers.silver_options import _SILVER_SCHEMA
    rows = {f.name: [] for f in _SILVER_SCHEMA}
    strikes = [70.0 + j * 2.5 for j in range(n // 2)]
    for j, strike in enumerate(strikes):
        for cp in ("C", "P"):
            rows["trade_date"].append("2026-06-24")
            rows["option_expiry"].append("2026-07-18")
            rows["option_symbol"].append(f"USO{j}{cp}")
            rows["underlying_contract"].append("USO")
            rows["underlying_delivery_month"].append("2026-07")
            rows["strike"].append(strike)
            rows["call_put"].append(cp)
            rows["settlement"].append(max(0.01, 2.0 - j * 0.1))
            rows["bid"].append(max(0.01, 1.9 - j * 0.1))
            rows["ask"].append(max(0.02, 2.1 - j * 0.1))
            rows["volume"].append(100)
            rows["open_interest"].append(500)
            rows["implied_volatility"].append(0.30 + j * 0.01)
            rows["delta"].append(0.5)
            rows["gamma"].append(0.02)
            rows["theta"].append(-0.03)
            rows["vega"].append(0.1)
            rows["exercise_style"].append("American")
            rows["settlement_style"].append("Equity_ETF")
            rows["contract_multiplier"].append(100)
            rows["source_id"].append("etrade_uso_options")
            rows["price_note"].append("USO_proxy")
            rows["raw_file_sha256"].append("abc")
            rows["silver_status"].append("PASS")
            rows["silver_note"].append("")
    return pa.table(rows, schema=_SILVER_SCHEMA)


def test_futures_section_pass(tmp_path):
    section = futures_section(_silver_futures_table(n=5, all_pass=True))
    assert section["status"] in ("PASS", "WARN")
    assert section["record_count"] == 5
    assert section["contract_count"] == 5


def test_futures_section_fail(tmp_path):
    section = futures_section(_silver_futures_table(n=5, all_pass=False))
    assert section["fail_count"] >= 1


def test_futures_section_none():
    section = futures_section(None)
    assert section["status"] == "INSUFFICIENT_DATA"


def test_options_section_pass():
    section = options_section(_silver_options_table(n=20))
    assert section["record_count"] == 20
    assert section["expiry_count"] >= 1


def test_options_section_none():
    section = options_section(None)
    assert section["status"] == "INSUFFICIENT_DATA"


def test_generate_creates_files(tmp_path):
    report = generate(
        trade_date=AS_OF,
        silver_futures=_silver_futures_table(),
        silver_options=_silver_options_table(),
        silver_eia=None,
        output_dir=tmp_path,
    )
    assert (tmp_path / "2026-06-24.json").exists()
    assert (tmp_path / "2026-06-24.md").exists()
    assert "overall_status" in report


def test_generate_json_is_valid(tmp_path):
    generate(
        trade_date=AS_OF,
        silver_futures=_silver_futures_table(),
        silver_options=_silver_options_table(),
        silver_eia=None,
        output_dir=tmp_path,
    )
    data = json.loads((tmp_path / "2026-06-24.json").read_text())
    assert data["trade_date"] == "2026-06-24"
    assert "futures" in data
    assert "options" in data
    assert "caveats" in data


def test_generate_no_data(tmp_path):
    report = generate(
        trade_date=AS_OF,
        silver_futures=None,
        silver_options=None,
        silver_eia=None,
        output_dir=tmp_path,
    )
    assert report["overall_status"] == "INSUFFICIENT_DATA"

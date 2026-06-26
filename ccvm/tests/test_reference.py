"""Tests for the WTI contract calendar reference data."""
from __future__ import annotations

from datetime import date

import pytest

from ccvm.reference.wti_calendar import (
    ContractInfo,
    active_contracts,
    contract_code,
    contract_info,
    futures_last_trade_date,
    option_expiry_date,
    parse_contract_code,
)


class TestFuturesLastTradeDate:
    def test_august_2026(self):
        # CLQ26: delivery Aug 2026
        # Prior month: July 2026
        # 25 July 2026 = Saturday → prior biz day = Friday 24 July
        # 3 biz days before July 24 = July 23 (Thu), 22 (Wed), 21 (Tue) → July 21
        ltd = futures_last_trade_date(2026, 8)
        assert ltd == date(2026, 7, 21)

    def test_september_2026(self):
        # CLU26: delivery Sep 2026
        # Prior month: Aug 2026
        # 25 Aug 2026 = Tuesday (business day) → ref = Aug 25
        # 3 biz days before Aug 25 = Aug 24, 21, 20 → Aug 20
        ltd = futures_last_trade_date(2026, 9)
        assert ltd == date(2026, 8, 20)

    def test_january_2027(self):
        # CLF27: delivery Jan 2027
        # Prior month: Dec 2026
        # 25 Dec 2026 = Friday → ref = Dec 25 (it's a business day since we don't count holidays)
        # 3 biz days before Dec 25 = Dec 24, 23, 22 → Dec 22
        ltd = futures_last_trade_date(2027, 1)
        assert ltd == date(2026, 12, 22)

    def test_result_is_weekday(self):
        for month in range(1, 13):
            ltd = futures_last_trade_date(2026, month)
            assert ltd.weekday() < 5, f"LTD for 2026-{month:02d} is {ltd} ({ltd.strftime('%A')})"


class TestOptionExpiryDate:
    def test_august_2026_is_before_futures_ltd(self):
        opt = option_expiry_date(2026, 8)
        ltd = futures_last_trade_date(2026, 8)
        assert opt < ltd

    def test_august_2026_is_6_business_days_before_ltd(self):
        # LTD = July 21 → go back 6 biz days:
        # July 20 (Mon), 17 (Fri), 16 (Thu), 15 (Wed), 14 (Tue), 13 (Mon) → July 13
        opt = option_expiry_date(2026, 8)
        assert opt == date(2026, 7, 13)

    def test_result_is_weekday(self):
        for month in range(1, 13):
            exp = option_expiry_date(2026, month)
            assert exp.weekday() < 5


class TestContractCode:
    def test_standard_codes(self):
        assert contract_code(2026, 8) == "CLQ26"
        assert contract_code(2026, 1) == "CLF26"
        assert contract_code(2026, 12) == "CLZ26"
        assert contract_code(2027, 3) == "CLH27"

    def test_all_months(self):
        expected = {
            1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
            7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
        }
        for m, letter in expected.items():
            assert contract_code(2026, m) == f"CL{letter}26"


class TestParseContractCode:
    def test_valid_codes(self):
        assert parse_contract_code("CLQ26") == (2026, 8)
        assert parse_contract_code("CLF27") == (2027, 1)
        assert parse_contract_code("CLZ26") == (2026, 12)

    def test_invalid_codes(self):
        assert parse_contract_code("CL") is None
        assert parse_contract_code("INVALID") is None
        assert parse_contract_code("") is None


class TestActiveContracts:
    def test_returns_correct_count(self):
        contracts = active_contracts(date(2026, 6, 25), num_months=12)
        assert len(contracts) == 12

    def test_contracts_sorted_by_delivery(self):
        contracts = active_contracts(date(2026, 6, 25), num_months=6)
        delivery_months = [c.delivery_month_str for c in contracts]
        assert delivery_months == sorted(delivery_months)

    def test_all_have_future_last_trade_date(self):
        as_of = date(2026, 6, 25)
        contracts = active_contracts(as_of, num_months=12)
        for c in contracts:
            assert c.last_trade_date >= as_of, f"{c.contract_code} LTD {c.last_trade_date} before {as_of}"

    def test_contract_info_consistency(self):
        info = contract_info(2026, 8)
        assert info.contract_code == "CLQ26"
        assert info.delivery_month_str == "2026-08"
        assert info.option_expiry < info.last_trade_date

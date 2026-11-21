"""Quarterly CME U.S. equity-index futures and options calendar.

This implementation intentionally scopes the bootstrap products to the
March/June/September/December quarterly contract family. Daily, weekly,
end-of-month, and serial option families are excluded because their exercise
and settlement conventions vary and cannot safely share one product profile.
Official exchange expiration files remain the acceptance source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .exchange_calendar import is_business_day
from .product import get_product

MONTH_LETTERS = {
    3: "H", 6: "M", 9: "U", 12: "Z",
}


@dataclass(frozen=True)
class ContractInfo:
    contract_code: str
    delivery_year: int
    delivery_month: int
    delivery_month_str: str
    last_trade_date: date
    option_expiry: date


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + timedelta(days=14)


def _previous_business_day(day: date) -> date:
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def futures_last_trade_date(delivery_year: int, delivery_month: int) -> date:
    if delivery_month not in MONTH_LETTERS:
        raise ValueError("Equity-index bootstrap supports quarterly months only")
    return _previous_business_day(_third_friday(delivery_year, delivery_month))


def option_expiry_date(delivery_year: int, delivery_month: int) -> date:
    return futures_last_trade_date(delivery_year, delivery_month)


def option_expiry_for_option_month(option_year: int, option_month: int) -> date:
    return option_expiry_date(option_year, option_month)


def contract_code(delivery_year: int, delivery_month: int) -> str:
    return (
        f"{get_product().futures_prefix}{MONTH_LETTERS[delivery_month]}"
        f"{str(delivery_year)[2:]}"
    )


def contract_info(delivery_year: int, delivery_month: int) -> ContractInfo:
    expiry = futures_last_trade_date(delivery_year, delivery_month)
    return ContractInfo(
        contract_code=contract_code(delivery_year, delivery_month),
        delivery_year=delivery_year,
        delivery_month=delivery_month,
        delivery_month_str=f"{delivery_year:04d}-{delivery_month:02d}",
        last_trade_date=expiry,
        option_expiry=expiry,
    )


def active_contracts(as_of_date: date, num_months: int = 4) -> list[ContractInfo]:
    result = []
    for year in range(as_of_date.year, as_of_date.year + 4):
        for month in (3, 6, 9, 12):
            info = contract_info(year, month)
            if info.last_trade_date >= as_of_date:
                result.append(info)
                if len(result) >= num_months:
                    return result
    return result

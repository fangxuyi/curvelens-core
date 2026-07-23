"""ICE Futures Europe Brent (B) futures and American-option calendar.

Futures cease on the last ICE Business Day of the second month before the
contract month, subject to the Christmas/New-Year adjustment. American options
cease three ICE Business Days before the futures, with additional Christmas,
New-Year, and U.S. Thanksgiving adjustments.

The exchange expiry table remains the acceptance source of truth. This module
encodes the standing rules and England/Wales bank holidays.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from .exchange_calendar import _easter_sunday, _last_weekday, _nth_weekday

MONTH_LETTERS = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}


@dataclass(frozen=True)
class ContractInfo:
    contract_code: str
    delivery_year: int
    delivery_month: int
    delivery_month_str: str
    last_trade_date: date
    option_expiry: date


def _substitute_fixed_holidays(year: int) -> tuple[date, date]:
    christmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    if christmas.weekday() == 5:
        return date(year, 12, 27), date(year, 12, 28)
    if christmas.weekday() == 6:
        return date(year, 12, 27), date(year, 12, 28)
    if boxing.weekday() == 5:
        return christmas, date(year, 12, 28)
    if boxing.weekday() == 6:
        return christmas, date(year, 12, 28)
    return christmas, boxing


@lru_cache(maxsize=64)
def ice_business_holidays(year: int) -> frozenset[date]:
    new_year = date(year, 1, 1)
    if new_year.weekday() == 5:
        new_year = date(year, 1, 3)
    elif new_year.weekday() == 6:
        new_year = date(year, 1, 2)
    easter = _easter_sunday(year)
    christmas, boxing = _substitute_fixed_holidays(year)
    return frozenset({
        new_year,
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        _nth_weekday(year, 5, 0, 1),
        _last_weekday(year, 5, 0),
        _last_weekday(year, 8, 0),
        christmas,
        boxing,
    })


def is_ice_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in ice_business_holidays(day.year)


def _previous_ice_business_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_ice_business_day(day):
        day -= timedelta(days=1)
    return day


def _business_day_before(day: date, holiday: date) -> bool:
    return day == _previous_ice_business_day(holiday)


def _us_thanksgiving(year: int) -> date:
    return _nth_weekday(year, 11, 3, 4)


def futures_last_trade_date(delivery_year: int, delivery_month: int) -> date:
    total = delivery_year * 12 + delivery_month - 1 - 2
    year, month_zero = divmod(total, 12)
    month = month_zero + 1
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    expiry = nxt - timedelta(days=1)
    while not is_ice_business_day(expiry):
        expiry -= timedelta(days=1)
    if (
        _business_day_before(expiry, date(expiry.year, 12, 25))
        or _business_day_before(expiry, date(expiry.year + 1, 1, 1))
    ):
        expiry = _previous_ice_business_day(expiry)
    return expiry


def option_expiry_date(delivery_year: int, delivery_month: int) -> date:
    expiry = futures_last_trade_date(delivery_year, delivery_month)
    for _ in range(3):
        expiry = _previous_ice_business_day(expiry)
    if (
        _business_day_before(expiry, date(expiry.year, 12, 25))
        or _business_day_before(expiry, date(expiry.year + 1, 1, 1))
        or expiry == _us_thanksgiving(expiry.year)
    ):
        expiry = _previous_ice_business_day(expiry)
    return expiry


def option_expiry_for_option_month(option_year: int, option_month: int) -> date:
    return option_expiry_date(option_year, option_month)


def contract_code(delivery_year: int, delivery_month: int) -> str:
    return f"B{MONTH_LETTERS[delivery_month]}{str(delivery_year)[2:]}"


def contract_info(delivery_year: int, delivery_month: int) -> ContractInfo:
    return ContractInfo(
        contract_code=contract_code(delivery_year, delivery_month),
        delivery_year=delivery_year,
        delivery_month=delivery_month,
        delivery_month_str=f"{delivery_year:04d}-{delivery_month:02d}",
        last_trade_date=futures_last_trade_date(delivery_year, delivery_month),
        option_expiry=option_expiry_date(delivery_year, delivery_month),
    )


def active_contracts(as_of_date: date, num_months: int = 12) -> list[ContractInfo]:
    result = []
    for offset in range(num_months + 3):
        total = as_of_date.month + offset - 1
        year = as_of_date.year + total // 12
        month = total % 12 + 1
        info = contract_info(year, month)
        if info.last_trade_date >= as_of_date:
            result.append(info)
            if len(result) >= num_months:
                break
    return result

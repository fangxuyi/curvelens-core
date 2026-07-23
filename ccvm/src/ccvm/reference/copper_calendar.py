"""COMEX Copper (HG futures / HX monthly options) contract calendar.

HG futures terminate on the third-last business day of the delivery month.
HX monthly options expire four business days before the end of the preceding
month, with the COMEX Friday/pre-holiday adjustment. Serial option months map
to the next March, May, July, September, or December HG future.

Sources: COMEX Rulebook Chapters 111 and 117 (reviewed 2026-07-23).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .exchange_calendar import is_business_day

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


def _month_end(year: int, month: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return nxt - timedelta(days=1)


def _previous_business_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def _subtract_business_days(day: date, count: int) -> date:
    while count:
        day -= timedelta(days=1)
        if is_business_day(day):
            count -= 1
    return day


def futures_last_trade_date(delivery_year: int, delivery_month: int) -> date:
    day = _month_end(delivery_year, delivery_month)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return _subtract_business_days(day, 2)


def option_expiry_for_option_month(option_year: int, option_month: int) -> date:
    if option_month == 1:
        prior_year, prior_month = option_year - 1, 12
    else:
        prior_year, prior_month = option_year, option_month - 1
    expiry = _month_end(prior_year, prior_month)
    remaining = 4
    while remaining:
        if is_business_day(expiry):
            remaining -= 1
            if remaining == 0:
                break
        expiry -= timedelta(days=1)
    next_day = expiry + timedelta(days=1)
    if expiry.weekday() == 4 or (next_day.weekday() < 5 and not is_business_day(next_day)):
        expiry = _previous_business_day(expiry)
    return expiry


def option_expiry_date(delivery_year: int, delivery_month: int) -> date:
    return option_expiry_for_option_month(delivery_year, delivery_month)


def contract_code(delivery_year: int, delivery_month: int) -> str:
    return f"HG{MONTH_LETTERS[delivery_month]}{str(delivery_year)[2:]}"


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
    for offset in range(60):
        total = as_of_date.month + offset - 1
        year = as_of_date.year + total // 12
        month = total % 12 + 1
        listed = offset < 24 or month in {3, 5, 7, 9, 12}
        if not listed:
            continue
        info = contract_info(year, month)
        if info.last_trade_date >= as_of_date:
            result.append(info)
            if len(result) >= num_months:
                break
    return result

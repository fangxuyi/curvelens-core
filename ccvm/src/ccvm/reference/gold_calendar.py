"""COMEX Gold (GC futures / OG monthly options) contract calendar.

GC futures stop trading on the third-last business day of the delivery month.
OG monthly options expire four business days before the end of the preceding
month, moved one business day earlier when that date is Friday or immediately
precedes an Exchange holiday.  Serial option months map to the next even-month
GC future through the product profile.

Sources: COMEX Rulebook Chapters 113 and 115 (reviewed 2026-07-16).
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


def _subtract_business_days(day: date, count: int) -> date:
    while count:
        day -= timedelta(days=1)
        if is_business_day(day):
            count -= 1
    return day


def _previous_business_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def futures_last_trade_date(delivery_year: int, delivery_month: int) -> date:
    """Third-last business day of the delivery month (COMEX Rule 113102.E)."""
    day = _month_end(delivery_year, delivery_month)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return _subtract_business_days(day, 2)


def option_expiry_for_option_month(option_year: int, option_month: int) -> date:
    """Monthly OG expiry keyed by its option contract month (Rule 115101.E)."""
    if option_month == 1:
        prior_year, prior_month = option_year - 1, 12
    else:
        prior_year, prior_month = option_year, option_month - 1
    expiry = _subtract_business_days(_month_end(prior_year, prior_month), 4)
    next_day = expiry + timedelta(days=1)
    if expiry.weekday() == 4 or (next_day.weekday() < 5 and not is_business_day(next_day)):
        expiry = _previous_business_day(expiry)
    return expiry


def option_expiry_date(delivery_year: int, delivery_month: int) -> date:
    """Expiry of the same-named monthly option for a GC delivery month."""
    return option_expiry_for_option_month(delivery_year, delivery_month)


def contract_code(delivery_year: int, delivery_month: int) -> str:
    return f"GC{MONTH_LETTERS[delivery_month]}{str(delivery_year)[2:]}"


def contract_info(delivery_year: int, delivery_month: int) -> ContractInfo:
    return ContractInfo(
        contract_code=contract_code(delivery_year, delivery_month),
        delivery_year=delivery_year,
        delivery_month=delivery_month,
        delivery_month_str=f"{delivery_year:04d}-{delivery_month:02d}",
        last_trade_date=futures_last_trade_date(delivery_year, delivery_month),
        option_expiry=option_expiry_date(delivery_year, delivery_month),
    )


def active_contracts(as_of_date: date, num_months: int = 36) -> list[ContractInfo]:
    result = []
    year, month = as_of_date.year, as_of_date.month
    for _ in range(num_months + 3):
        info = contract_info(year, month)
        if info.last_trade_date >= as_of_date:
            result.append(info)
            if len(result) >= num_months:
                break
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result

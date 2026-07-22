"""CBOT Corn (ZC futures / standard Corn options) contract calendar.

Corn futures stop trading on the business day before the 15th calendar day of
the delivery month. Standard and serial options stop on the last Friday that
precedes by at least two business days the last business day of the month
before the named option month; a Friday holiday moves expiry to the prior
business day.

Sources: CBOT Rulebook Chapters 10 and 10A; CME Section 56 expiry table,
reviewed 2026-07-21.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .exchange_calendar import is_business_day

MONTH_LETTERS = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
LISTED_FUTURES_MONTHS = (3, 5, 7, 9, 12)


@dataclass(frozen=True)
class ContractInfo:
    contract_code: str
    delivery_year: int
    delivery_month: int
    delivery_month_str: str
    last_trade_date: date
    option_expiry: date


def _previous_business_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def _month_end(year: int, month: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return nxt - timedelta(days=1)


def _business_days_from_candidate(start: date, end: date) -> int:
    """Business days from candidate through the day before month-end business day."""
    count = 0
    day = start
    while day < end:
        if is_business_day(day):
            count += 1
        day += timedelta(days=1)
    return count


def futures_last_trade_date(delivery_year: int, delivery_month: int) -> date:
    """Business day immediately before the delivery month's 15th."""
    return _previous_business_day(date(delivery_year, delivery_month, 15))


def option_expiry_for_option_month(option_year: int, option_month: int) -> date:
    """Standard/serial Corn option expiry keyed by the named option month."""
    if option_month == 1:
        prior_year, prior_month = option_year - 1, 12
    else:
        prior_year, prior_month = option_year, option_month - 1
    last_business = _month_end(prior_year, prior_month)
    while not is_business_day(last_business):
        last_business -= timedelta(days=1)
    friday = last_business
    while friday.weekday() != 4:
        friday -= timedelta(days=1)
    while _business_days_from_candidate(friday, last_business) < 2:
        friday -= timedelta(days=7)
    if not is_business_day(friday):
        return _previous_business_day(friday + timedelta(days=1))
    return friday


def option_expiry_date(delivery_year: int, delivery_month: int) -> date:
    return option_expiry_for_option_month(delivery_year, delivery_month)


def contract_code(delivery_year: int, delivery_month: int) -> str:
    return f"ZC{MONTH_LETTERS[delivery_month]}{str(delivery_year)[2:]}"


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
    year, month = as_of_date.year, as_of_date.month
    for _ in range(num_months * 4):
        if month in LISTED_FUTURES_MONTHS:
            info = contract_info(year, month)
            if info.last_trade_date >= as_of_date:
                result.append(info)
                if len(result) >= num_months:
                    break
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result

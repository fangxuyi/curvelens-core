"""
WTI Crude Oil (NYMEX CL / LO) contract calendar.

Last Trading Day calculation:
    CME Rule: "Trading terminates at the close of business on the third business
    day prior to the 25th calendar day of the month preceding the delivery month.
    If the 25th calendar day is not a business day, trading terminates on the
    third business day prior to the last business day preceding the 25th."

LO Option Expiry:
    Trading terminates 3 business days before the termination of trading in
    the underlying futures contract: option_expiry = futures_LTD − 3 business
    days.

Business days are Mon–Fri excluding CME full-closure holidays
(see exchange_calendar.py).

Verified against external sources (2026-07-10):
  - ICE WTI American-style options expiry table (mirrors the NYMEX LO
    schedule), delivery-month labels: Aug26→2026-07-16, Sep26→2026-08-17,
    Oct26→2026-09-17, Nov26→2026-10-15, Dec26→2026-11-17
  - Documented CLK20 LTD 2020-04-21 and LOK20 expiry 2020-04-16
  - ICE WTI futures LTDs equal NYMEX CL LTD − 1 business day (ICE uses a
    4-days-prior rule); all six checked months align, including the
    Christmas-shifted CLF27 case (NYMEX LTD 2026-12-21).

Pinned fixtures: tests/fixtures/cme_expiry_calendar.json
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .exchange_calendar import is_business_day as _cme_is_business_day

MONTH_LETTERS: dict[int, str] = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
LETTER_MONTHS: dict[str, int] = {v: k for k, v in MONTH_LETTERS.items()}


@dataclass(frozen=True)
class ContractInfo:
    contract_code: str        # e.g. "CLQ26"
    delivery_year: int
    delivery_month: int
    delivery_month_str: str   # YYYY-MM
    last_trade_date: date
    option_expiry: date


def _is_business_day(d: date) -> bool:
    return _cme_is_business_day(d)  # Mon–Fri excluding CME holidays


def _prev_business_day(d: date) -> date:
    """Return d if it's a business day, else the prior Friday."""
    while not _is_business_day(d):
        d -= timedelta(days=1)
    return d


def _subtract_business_days(d: date, n: int) -> date:
    """Subtract exactly n business days from d."""
    while n > 0:
        d -= timedelta(days=1)
        if _is_business_day(d):
            n -= 1
    return d


def futures_last_trade_date(delivery_year: int, delivery_month: int) -> date:
    """
    Compute the NYMEX CL futures last trading day for a given delivery month.

    Algorithm:
      1. anchor = 25th of the month prior to delivery
      2. ref    = anchor if business day, else prev business day
      3. LTD    = ref minus 3 business days
    """
    # Month prior to delivery
    if delivery_month == 1:
        prior_year, prior_month = delivery_year - 1, 12
    else:
        prior_year, prior_month = delivery_year, delivery_month - 1

    anchor = date(prior_year, prior_month, 25)
    ref = _prev_business_day(anchor)
    return _subtract_business_days(ref, 3)


def option_expiry_date(delivery_year: int, delivery_month: int) -> date:
    """
    Compute the LO (WTI options) expiry date: 3 business days before the
    underlying futures last trading day.

    Verified against the ICE WTI American options schedule (mirrors NYMEX LO)
    and the documented LOK20 expiry (2020-04-16).
    """
    ltd = futures_last_trade_date(delivery_year, delivery_month)
    return _subtract_business_days(ltd, 3)


def contract_code(delivery_year: int, delivery_month: int) -> str:
    letter = MONTH_LETTERS[delivery_month]
    y2 = str(delivery_year)[2:]
    return f"CL{letter}{y2}"


def contract_info(delivery_year: int, delivery_month: int) -> ContractInfo:
    code = contract_code(delivery_year, delivery_month)
    ltd = futures_last_trade_date(delivery_year, delivery_month)
    opt_exp = option_expiry_date(delivery_year, delivery_month)
    return ContractInfo(
        contract_code=code,
        delivery_year=delivery_year,
        delivery_month=delivery_month,
        delivery_month_str=f"{delivery_year:04d}-{delivery_month:02d}",
        last_trade_date=ltd,
        option_expiry=opt_exp,
    )


def active_contracts(as_of_date: date, num_months: int = 36) -> list[ContractInfo]:
    """
    Return ContractInfo for contracts whose last trade date is on or after as_of_date,
    up to num_months forward contracts.
    """
    result: list[ContractInfo] = []
    year, month = as_of_date.year, as_of_date.month
    seen = 0
    # Scan enough months to find num_months active contracts
    for _ in range(num_months + 3):
        info = contract_info(year, month)
        if info.last_trade_date >= as_of_date:
            result.append(info)
            seen += 1
            if seen >= num_months:
                break
        month += 1
        if month > 12:
            month = 1
            year += 1
    return result


def parse_contract_code(code: str) -> tuple[int, int] | None:
    """
    Parse 'CLQ26' → (2026, 8).  Returns None if unparseable.
    Handles codes like 'CL', 'CLQ26', etc.
    """
    if len(code) < 4:
        return None
    # Find the letter position (should be index 2 for CL contracts)
    try:
        letter = code[2]
        month = LETTER_MONTHS.get(letter)
        year = 2000 + int(code[3:])
        if month is None:
            return None
        return year, month
    except (ValueError, IndexError):
        return None

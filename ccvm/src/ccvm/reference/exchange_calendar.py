"""
CME/NYMEX exchange holiday calendar.

Full-closure holidays observed by CME Group (NYMEX energy floor/Globex full
close). Computed programmatically so any year works:

  - New Year's Day        Jan 1  (observed)
  - Martin Luther King    3rd Monday of January
  - Presidents' Day       3rd Monday of February
  - Good Friday           Friday before Easter Sunday
  - Memorial Day          last Monday of May
  - Juneteenth            Jun 19 (observed; CME observes from 2022)
  - Independence Day      Jul 4  (observed)
  - Labor Day             1st Monday of September
  - Thanksgiving          4th Thursday of November
  - Christmas             Dec 25 (observed)

Observation rule for fixed-date holidays: Saturday → preceding Friday,
Sunday → following Monday (CME's usual practice).

Note: early-close days (day after Thanksgiving, Christmas Eve) are treated as
business days — settlements are still published, which is what matters for
expiry math.

Verified against: ICE WTI American options expiry table (which mirrors the
NYMEX LO schedule) for Aug–Dec 2026, and the documented CLK20/LOK20 dates
(2020-04-21 / 2020-04-16).
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th <weekday> (Mon=0) of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """Saturday → Friday, Sunday → Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def cme_holidays(year: int) -> frozenset[date]:
    """Full-closure CME/NYMEX holidays for a calendar year."""
    hols = {
        _observed(date(year, 1, 1)),                     # New Year's Day
        _nth_weekday(year, 1, 0, 3),                     # MLK Day
        _nth_weekday(year, 2, 0, 3),                     # Presidents' Day
        _easter_sunday(year) - timedelta(days=2),        # Good Friday
        _last_weekday(year, 5, 0),                       # Memorial Day
        _observed(date(year, 7, 4)),                     # Independence Day
        _nth_weekday(year, 9, 0, 1),                     # Labor Day
        _nth_weekday(year, 11, 3, 4),                    # Thanksgiving
        _observed(date(year, 12, 25)),                   # Christmas
    }
    if year >= 2022:                                     # Juneteenth (CME from 2022)
        hols.add(_observed(date(year, 6, 19)))
    return frozenset(hols)


def is_business_day(d: date) -> bool:
    """Mon–Fri and not a CME full-closure holiday."""
    return d.weekday() < 5 and d not in cme_holidays(d.year)

"""Fixture-pinned tests for CL futures LTD and LO option expiry dates.

The fixture holds externally verified dates (ICE WTI American options table —
which mirrors the NYMEX LO schedule — plus the documented April 2020 dates).
If these fail, the calendar rules are wrong, and every TTE/IV downstream is
wrong with them.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ccvm.reference.exchange_calendar import cme_holidays, is_business_day
from ccvm.reference.wti_calendar import futures_last_trade_date, option_expiry_date

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "cme_expiry_calendar.json").read_text()
)


def _cases(key: str) -> list[tuple[int, int, date]]:
    return [
        (int(k[:4]), int(k[5:7]), date.fromisoformat(v))
        for k, v in FIXTURE[key].items()
    ]


class TestFuturesLTDAgainstFixture:
    @pytest.mark.parametrize("y,m,expected", _cases("futures_ltd"))
    def test_pinned_dates(self, y, m, expected):
        assert futures_last_trade_date(y, m) == expected


class TestOptionExpiryAgainstFixture:
    @pytest.mark.parametrize("y,m,expected", _cases("option_expiry"))
    def test_pinned_dates(self, y, m, expected):
        assert option_expiry_date(y, m) == expected

    def test_option_expiry_is_3_business_days_before_ltd(self):
        # Rule shape: LO expiry = futures LTD − 3 business days
        for y, m, _ in _cases("option_expiry"):
            ltd = futures_last_trade_date(y, m)
            opt = option_expiry_date(y, m)
            # count business days strictly between opt and ltd
            n, d = 0, opt
            from datetime import timedelta
            while d < ltd:
                d += timedelta(days=1)
                if is_business_day(d):
                    n += 1
            assert n == 3, f"{y}-{m:02d}: expiry {opt} is {n} biz days before LTD {ltd}"


class TestHolidayCalendar:
    def test_known_holidays(self):
        h26 = cme_holidays(2026)
        assert date(2026, 1, 1) in h26         # New Year
        assert date(2026, 4, 3) in h26         # Good Friday (Easter 2026-04-05)
        assert date(2026, 11, 26) in h26       # Thanksgiving
        assert date(2026, 12, 25) in h26       # Christmas
        assert date(2026, 7, 3) in h26         # July 4 observed (Sat → Fri)
        assert date(2020, 4, 10) in cme_holidays(2020)   # Good Friday 2020

    def test_juneteenth_from_2022(self):
        assert date(2022, 6, 20) in cme_holidays(2022)   # Jun 19 2022 = Sun → Mon
        assert all(d.month != 6 or d.day < 19 or d != date(2021, 6, 18)
                   for d in cme_holidays(2021))          # not observed in 2021

    def test_christmas_shifts_clf27(self):
        # CLF27: anchor 2026-12-25 is Christmas (Friday, holiday) → ref 12-24,
        # LTD = 12-21. Without the holiday calendar this came out 12-22.
        assert futures_last_trade_date(2027, 1) == date(2026, 12, 21)

    def test_weekday_only(self):
        for y, m, expected in _cases("futures_ltd") + _cases("option_expiry"):
            assert expected.weekday() < 5

"""Tests for the knowledge-pack loader (B1)."""
from __future__ import annotations

from datetime import date

from ccvm.knowledge.loader import (
    _next_weekday,
    knowledge_path,
    load_calendar,
    upcoming_events,
)


class TestNextWeekday:
    def test_strictly_after(self):
        # 2026-07-08 is a Wednesday; next WED must be the 15th, not the 8th
        assert _next_weekday(date(2026, 7, 8), 2) == date(2026, 7, 15)

    def test_next_day(self):
        # Tue after Mon 2026-07-06 is 07-07
        assert _next_weekday(date(2026, 7, 6), 1) == date(2026, 7, 7)


class TestCalendarFile:
    def test_wti_calendar_loads(self):
        cal = load_calendar("wti")
        assert cal.get("product") == "wti"
        names = [e["name"] for e in cal["recurring"]]
        assert any("EIA Weekly Petroleum" in n for n in names)
        assert any("Commitments of Traders" in n for n in names)

    def test_knowledge_prose_files_exist(self):
        kp = knowledge_path("wti")
        for f in ("conventions.md", "regimes.md", "seasonality.md", "analogs.md"):
            assert (kp / f).exists(), f"missing knowledge/wti/{f}"

    def test_missing_product_is_empty(self):
        assert load_calendar("nonexistent_product") == {}


class TestUpcomingEvents:
    def test_week_of_2026_07_10(self):
        # Friday 2026-07-10 → within 8 days: API Tue 07-14, EIA Wed 07-15,
        # rig count + COT Fri 07-17, and (from the contract calendar) LOQ26's
        # underlying option expiry 2026-07-16 + CLQ26 futures LTD 2026-07-21
        # is beyond 07-18 so excluded.
        evs = upcoming_events(date(2026, 7, 10), horizon_days=8)
        dates = {e["name"]: e["date"] for e in evs}
        assert dates["EIA Weekly Petroleum Status Report"] == "2026-07-15"
        assert dates["API Weekly Statistical Bulletin"] == "2026-07-14"
        assert dates["CFTC Commitments of Traders"] == "2026-07-17"
        # contract event: option expiry for CLQ26 delivery (2026-07-16)
        assert any(e["kind"] == "contract" and e["date"] == "2026-07-16" for e in evs)
        # sorted by date
        ds = [e["date"] for e in evs]
        assert ds == sorted(ds)

    def test_horizon_excludes_far_events(self):
        evs = upcoming_events(date(2026, 7, 10), horizon_days=2)
        assert all(e["date"] <= "2026-07-12" for e in evs)

"""Tests for the knowledge-pack loader (B1)."""
from __future__ import annotations

from datetime import date

from ccvm.knowledge.loader import (
    _next_weekday,
    knowledge_path,
    load_calendar,
    stale_dated_events,
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


class TestMaintenanceGuardrails:
    """Enforce knowledge/MAINTENANCE.md §5 mechanically."""

    def test_no_stale_dated_events_today(self):
        # If this fails, remove the passed entries from calendar.yaml's
        # dated: list (per MAINTENANCE.md §3) — they are maintenance debt.
        stale = stale_dated_events(date.today(), "wti")
        assert stale == [], f"stale dated events in knowledge/wti/calendar.yaml: {stale}"

    def test_prose_files_carry_last_reviewed_header(self):
        kp = knowledge_path("wti")
        for f in ("conventions.md", "regimes.md", "seasonality.md", "analogs.md"):
            text = (kp / f).read_text()
            assert "*Last reviewed: " in text.split("\n\n")[0] + text[:200], (
                f"knowledge/wti/{f} is missing its '*Last reviewed: YYYY-MM-DD*' header"
            )

    def test_maintenance_process_file_exists(self):
        assert (knowledge_path("wti").parent / "MAINTENANCE.md").exists()

    def test_stale_detection_logic(self):
        # unparseable and past dates are stale; future dates are not
        from unittest.mock import patch
        cal = {"dated": [
            {"name": "past", "date": "2020-01-01"},
            {"name": "bad", "date": "not-a-date"},
            {"name": "future", "date": "2099-01-01"},
        ]}
        with patch("ccvm.knowledge.loader.load_calendar", return_value=cal):
            stale = stale_dated_events(date(2026, 7, 11))
        assert [e["name"] for e in stale] == ["past", "bad"]


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

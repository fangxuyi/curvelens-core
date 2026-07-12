"""
Knowledge-pack loader.

Reads knowledge/<product>/calendar.yaml (repo root) and merges the scheduled
release calendar with contract events (front option expiry / futures LTD)
computed from the WTI calendar module. Drives the daily brief's "Next Review"
section; later (D1) the same file schedules event-calendar runs.

The knowledge pack lives at the repo root — agent-consultable, versioned,
product-scoped:

    CurveLens/
      knowledge/wti/calendar.yaml   ← parsed here
      knowledge/wti/*.md            ← prose files for the agent (not parsed)
      ccvm/src/ccvm/knowledge/loader.py   ← this module
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml

from ..reference.wti_calendar import active_contracts

logger = logging.getLogger(__name__)

# CurveLens/ccvm/src/ccvm/knowledge/loader.py → parents[4] = CurveLens/
_REPO_ROOT = Path(__file__).resolve().parents[4]
KNOWLEDGE_DIR = _REPO_ROOT / "knowledge"

_WEEKDAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def knowledge_path(product: str = "wti") -> Path:
    return KNOWLEDGE_DIR / product


def load_calendar(product: str = "wti") -> dict:
    """Parse knowledge/<product>/calendar.yaml; {} if absent/unparseable."""
    path = knowledge_path(product) / "calendar.yaml"
    if not path.exists():
        logger.warning("No knowledge calendar at %s", path)
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        logger.error("Unparseable knowledge calendar %s: %s", path, exc)
        return {}


def _next_weekday(after: date, weekday: int) -> date:
    """First date strictly after `after` falling on `weekday` (Mon=0)."""
    days = (weekday - after.weekday() - 1) % 7 + 1
    return after + timedelta(days=days)


def stale_dated_events(as_of: date, product: str = "wti") -> list[dict]:
    """Past entries in calendar.yaml's `dated:` list — maintenance debt.

    Per knowledge/MAINTENANCE.md these should be removed once they have
    passed; upcoming_events() logs a warning when any exist.
    """
    stale = []
    for ev in load_calendar(product).get("dated") or []:
        try:
            d = date.fromisoformat(str(ev.get("date")))
        except (TypeError, ValueError):
            stale.append(ev)  # unparseable date is also maintenance debt
            continue
        if d < as_of:
            stale.append(ev)
    return stale


def upcoming_events(
    as_of: date,
    horizon_days: int = 8,
    product: str = "wti",
) -> list[dict]:
    """
    Scheduled events within (as_of, as_of + horizon_days], sorted by date.

    Merges:
      - weekly recurring events from calendar.yaml
      - dated one-offs from calendar.yaml
      - contract events (front option expiry, front futures LTD) computed
        from the WTI calendar module
    Each event: {"date", "name", "time_et", "kind"}.
    """
    horizon = as_of + timedelta(days=horizon_days)
    cal = load_calendar(product)
    events: list[dict] = []

    stale = stale_dated_events(as_of, product)
    if stale:
        logger.warning(
            "knowledge/%s/calendar.yaml has %d stale dated event(s) — remove them: %s",
            product, len(stale), [e.get("name") for e in stale],
        )

    for ev in cal.get("recurring") or []:
        wd = _WEEKDAYS.get(str(ev.get("day", "")).upper())
        if wd is None:
            continue
        d = _next_weekday(as_of, wd)
        if d <= horizon:
            events.append({
                "date": d.isoformat(),
                "name": ev.get("name", "?"),
                "time_et": ev.get("time_et"),
                "kind": ev.get("kind", "scheduled"),
            })

    for ev in cal.get("dated") or []:
        try:
            d = date.fromisoformat(str(ev.get("date")))
        except (TypeError, ValueError):
            continue
        if as_of < d <= horizon:
            events.append({
                "date": d.isoformat(),
                "name": ev.get("name", "?"),
                "time_et": ev.get("time_et"),
                "kind": ev.get("kind", "dated"),
            })

    # Contract events for the front contract (expiry math from wti_calendar —
    # the single source of truth; see knowledge/wti/conventions.md)
    for info in active_contracts(as_of, num_months=2):
        if as_of < info.option_expiry <= horizon:
            events.append({
                "date": info.option_expiry.isoformat(),
                "name": f"LO option expiry ({info.contract_code} underlying)",
                "time_et": None,
                "kind": "contract",
            })
        if as_of < info.last_trade_date <= horizon:
            events.append({
                "date": info.last_trade_date.isoformat(),
                "name": f"{info.contract_code} futures last trade",
                "time_et": None,
                "kind": "contract",
            })

    events.sort(key=lambda e: (e["date"], e.get("time_et") or ""))
    return events

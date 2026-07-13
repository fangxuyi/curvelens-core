"""
EIA seasonal context + seasonally-adjusted scenario triggers (B4).

A 4 MMbbl June draw is seasonal; the same draw in January is a signal
(knowledge/wti/seasonality.md). The raw crude-stocks file now carries 5 years
of weekly history (collector length=260), so instead of judging the raw WoW
change against fixed ±3 MMbbl thresholds year-round, we judge the **surprise
vs the seasonal norm** for that week of year:

    surprise_draw = actual_draw − seasonal_avg_draw(week_of_year)

and apply the same thresholds to the surprise. The fixed-threshold trigger is
kept as fallback (insufficient history) and for disagreement logging.

Seasonal stats use prior-year observations within ±1 week of the same
week-of-year (≥3 samples required).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SERIES_ID = "WCESTUS1"  # U.S. ending stocks excluding SPR
_MIN_SAMPLES = 3
_BULL, _BEAR_WATCH, _BEAR = 3_000, -2_000, -4_000  # same thresholds, applied to surprise


def _find_raw_crude(data_dir: Path, as_of: date) -> Optional[Path]:
    base = data_dir / "raw" / "eia_api_v2"
    if not base.exists():
        return None
    target = f"eia_us_crude_stocks_{as_of.strftime('%Y%m%d')}.json"
    candidates = []
    for child in sorted(base.iterdir(), reverse=True):
        if child.is_dir():
            for f in child.glob("eia_us_crude_stocks_*.json"):
                if f.name.endswith(".meta.json"):
                    continue
                if f.name <= target:
                    candidates.append((f.name, f))
    return max(candidates)[1] if candidates else None


def load_crude_levels(data_dir: Path, as_of: date) -> dict[str, float]:
    """{period: level_mbbl} for the ex-SPR crude series from the latest raw file."""
    p = _find_raw_crude(data_dir, as_of)
    if p is None:
        return {}
    try:
        rows = json.loads(p.read_text())["response"]["data"]
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Unreadable EIA raw file %s", p)
        return {}
    out = {}
    for r in rows:
        if r.get("series") == _SERIES_ID and r.get("value") is not None:
            try:
                out[r["period"]] = float(r["value"])
            except (TypeError, ValueError):
                continue
    return out


def _weekly_changes(levels: dict[str, float]) -> dict[str, float]:
    """{period: WoW change} (positive = build) from consecutive weekly levels."""
    periods = sorted(levels)
    return {
        periods[i]: levels[periods[i]] - levels[periods[i - 1]]
        for i in range(1, len(periods))
    }


def _week_of_year(period: str) -> int:
    return date.fromisoformat(period).isocalendar()[1]


def seasonal_stats(changes: dict[str, float], latest_period: str) -> Optional[dict]:
    """5y stats of WoW changes at this week-of-year (±1 week), prior years only."""
    woy = _week_of_year(latest_period)
    latest_year = int(latest_period[:4])
    samples = []
    for p, chg in changes.items():
        if p == latest_period or int(p[:4]) >= latest_year:
            continue
        d = abs(_week_of_year(p) - woy)
        if min(d, 52 - d) <= 1:
            samples.append(chg)
    if len(samples) < _MIN_SAMPLES:
        return None
    return {
        "n_samples": len(samples),
        "avg_change": sum(samples) / len(samples),
        "min_change": min(samples),
        "max_change": max(samples),
    }


def _trigger_from_draw(draw: float) -> str:
    if draw > _BULL:
        return "bull_confirmed"
    if draw < _BEAR:
        return "bear_confirmed"
    if draw < _BEAR_WATCH:
        return "bear_watch"
    return "none"


def compute(data_dir: Path, as_of_str: str) -> Optional[dict]:
    """Seasonal EIA context for the latest report period, or None if no data.

    Returns actual/seasonal changes, surprise, level-vs-5y-avg, the
    seasonally-adjusted trigger, the fixed trigger, and whether they disagree.
    """
    levels = load_crude_levels(data_dir, date.fromisoformat(as_of_str))
    if len(levels) < 10:
        return None
    changes = _weekly_changes(levels)
    latest_period = max(changes)
    actual_change = changes[latest_period]     # positive = build
    actual_draw = -actual_change               # positive = draw

    stats = seasonal_stats(changes, latest_period)
    fixed_trigger = _trigger_from_draw(actual_draw)

    out = {
        "eia_period": latest_period,
        "actual_change_mbbl": actual_change,
        "actual_draw_mbbl": actual_draw,
        "fixed_trigger": fixed_trigger,
        "weeks_of_history": len(levels),
    }

    if stats is None:
        out.update({
            "seasonal_available": False,
            "trigger": fixed_trigger,
            "trigger_basis": "fixed_fallback",
        })
        return out

    seasonal_avg_draw = -stats["avg_change"]
    surprise_draw = actual_draw - seasonal_avg_draw
    seasonal_trigger = _trigger_from_draw(surprise_draw)

    # Level vs the 5y average level at this week-of-year
    woy = _week_of_year(latest_period)
    latest_year = int(latest_period[:4])
    level_samples = [
        lv for p, lv in levels.items()
        if int(p[:4]) < latest_year
        and min(abs(_week_of_year(p) - woy), 52 - abs(_week_of_year(p) - woy)) <= 1
    ]
    level_vs_5y = (
        levels.get(latest_period) - sum(level_samples) / len(level_samples)
        if level_samples and levels.get(latest_period) is not None else None
    )

    if seasonal_trigger != fixed_trigger:
        logger.info(
            "EIA trigger disagreement: seasonal=%s vs fixed=%s "
            "(draw %.0f vs seasonal avg draw %.0f → surprise %.0f MBBL)",
            seasonal_trigger, fixed_trigger, actual_draw, seasonal_avg_draw, surprise_draw,
        )

    out.update({
        "seasonal_available": True,
        "seasonal_n_samples": stats["n_samples"],
        "seasonal_avg_change_mbbl": stats["avg_change"],
        "seasonal_avg_draw_mbbl": seasonal_avg_draw,
        "surprise_draw_mbbl": surprise_draw,
        "level_vs_5y_avg_mbbl": level_vs_5y,
        "trigger": seasonal_trigger,
        "trigger_basis": "seasonal_surprise",
        "disagrees_with_fixed": seasonal_trigger != fixed_trigger,
    })
    return out

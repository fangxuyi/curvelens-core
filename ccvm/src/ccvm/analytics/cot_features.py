"""
COT positioning features (B3).

From the raw weekly COT rows (collectors/cftc_cot.py): the latest report as
of the trade date, managed-money net length + WoW change + trailing 1y/3y
percentiles, and producer/merchant net as the commercial side.

Positions are as of Tuesday and published Friday 15:30 ET — `report_date`
and `published_note` carry the lag so the brief can label it.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from ..collectors.cftc_cot import load_cot_rows
from .history_context import percentile_of

logger = logging.getLogger(__name__)


def compute(data_dir: Path, as_of_str: str) -> Optional[dict]:
    """COT context dict for the brief, or None if no data on disk."""
    rows = load_cot_rows(data_dir, date.fromisoformat(as_of_str))
    rows = [r for r in rows if r["report_date"] <= as_of_str]
    if not rows:
        return None
    rows.sort(key=lambda r: r["report_date"])

    def _mm_net(r):
        return r["mm_long"] - r["mm_short"]

    latest = rows[-1]
    prior = rows[-2] if len(rows) >= 2 else None
    mm_net = _mm_net(latest)
    mm_net_series_1y = [_mm_net(r) for r in rows[-52:]]
    mm_net_series_3y = [_mm_net(r) for r in rows[-156:]]

    return {
        "report_date": latest["report_date"],
        "published_note": "positions as of Tuesday; published Friday 15:30 ET",
        "mm_long": latest["mm_long"],
        "mm_short": latest["mm_short"],
        "mm_net": mm_net,
        "mm_net_wow": (mm_net - _mm_net(prior)) if prior else None,
        "mm_net_pctile_1y": percentile_of(mm_net_series_1y, mm_net),
        "mm_net_pctile_3y": percentile_of(mm_net_series_3y, mm_net),
        "prod_net": latest["prod_long"] - latest["prod_short"],
        "open_interest": latest["open_interest"],
        "weeks_of_history": len(rows),
    }

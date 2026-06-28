"""
EIA bronze → silver normalization.

Maps EIA series IDs to named keys, selects headline series, computes
week-over-week change from the historical rows in each file, and assigns
a quality status.

One silver row per (series_id, period) — the full trailing history is kept
so the gold layer can compute multi-week trends.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pyarrow as pa

# Map EIA series ID → human-readable key
SERIES_KEY_MAP: dict[str, str] = {
    "WCRSTUS1":             "us_crude_total",          # total incl. SPR (MBBL)
    "WCESTUS1":             "us_crude_ex_spr",         # excl. SPR (MBBL) ★ headline
    "WCSSTUS1":             "us_spr_stocks",           # SPR (MBBL)
    "W_EPC0_SKA_NUS_MBBL":  "us_crude_transit",        # in-transit from Alaska (MBBL)
    "W_EPC0_SAX_YCUOK_MBBL":"cushing_stocks",          # Cushing, OK (MBBL) ★ headline
    "WCRIMUS2":             "us_crude_imports",        # imports (MBBL/D) ★ headline
    "WCREXUS2":             "us_crude_exports",        # exports (MBBL/D) ★ headline
    "WPULEUS3":             "refinery_utilization_pct",# % operable capacity ★ headline
    "WGIRIUS2":             "gross_refinery_inputs",   # MBBL/D
    "WCRRIUS2":             "crude_refinery_inputs",   # MBBL/D
    "WGTSTUS1":             "gasoline_stocks",         # MBBL ★ headline
    "WDISTUS1":             "distillate_stocks",       # MBBL ★ headline
}

HEADLINE_KEYS = {
    "us_crude_ex_spr",
    "cushing_stocks",
    "us_crude_imports",
    "us_crude_exports",
    "refinery_utilization_pct",
    "gasoline_stocks",
    "distillate_stocks",
}

_SCHEMA = pa.schema([
    pa.field("trade_date",        pa.string()),
    pa.field("period",            pa.string()),   # EIA week-end date YYYY-MM-DD
    pa.field("series_id",         pa.string()),
    pa.field("series_key",        pa.string()),   # named enum or series_id if unknown
    pa.field("series_description",pa.string()),
    pa.field("value",             pa.float64()),
    pa.field("units",             pa.string()),
    pa.field("geography",         pa.string()),
    pa.field("is_headline",       pa.bool_()),
    pa.field("is_latest_period",  pa.bool_()),    # True for most-recent period row
    pa.field("wow_change",        pa.float64()),  # value - prior_week_value (None if <2 periods)
    pa.field("wow_pct_change",    pa.float64()),  # wow_change / prior_week_value
    pa.field("silver_status",     pa.string()),   # PASS / WARN / FAIL
    pa.field("silver_note",       pa.string()),
    pa.field("source_id",         pa.string()),
    pa.field("raw_file_sha256",   pa.string()),
])


def normalize(bronze: pa.Table, as_of_date: date) -> pa.Table:
    """
    Normalize EIA bronze table into silver.
    bronze: output of bronze_eia.parse() — may contain multiple files concatenated.
    """
    d = bronze.to_pydict()
    n = len(d["period"])

    # Group rows by series_id, deduplicate by period (keep first seen), sort newest-first
    from collections import defaultdict
    by_series: dict[str, list[dict]] = defaultdict(list)
    seen_keys: set[tuple] = set()
    for i in range(n):
        row = {col: d[col][i] for col in d}
        key = (row["series_id"], row["period"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        by_series[row["series_id"]].append(row)

    for sid in by_series:
        by_series[sid].sort(key=lambda r: r["period"], reverse=True)

    out: dict[str, list] = {f.name: [] for f in _SCHEMA}

    for sid, rows in by_series.items():
        series_key = SERIES_KEY_MAP.get(sid, sid)
        is_headline = series_key in HEADLINE_KEYS

        for j, row in enumerate(rows):
            val = row["value"]
            prior_val = rows[j + 1]["value"] if j + 1 < len(rows) else None

            # WoW change
            if val is not None and prior_val is not None:
                wow = val - prior_val
                wow_pct = wow / prior_val if prior_val != 0 else None
            else:
                wow = None
                wow_pct = None

            # Quality status
            if val is None:
                status = "FAIL"
                note = "null value from EIA"
            elif val < 0 and series_key not in ("us_crude_imports", "us_crude_exports"):
                status = "WARN"
                note = f"negative stock level: {val}"
            else:
                status = "PASS"
                note = ""

            out["trade_date"].append(as_of_date.isoformat())
            out["period"].append(row["period"])
            out["series_id"].append(sid)
            out["series_key"].append(series_key)
            out["series_description"].append(row["series_description"])
            out["value"].append(val)
            out["units"].append(row["units"])
            out["geography"].append(row["geography"])
            out["is_headline"].append(is_headline)
            out["is_latest_period"].append(j == 0)
            out["wow_change"].append(wow)
            out["wow_pct_change"].append(wow_pct)
            out["silver_status"].append(status)
            out["silver_note"].append(note)
            out["source_id"].append(row["source_id"])
            out["raw_file_sha256"].append(row["raw_file_sha256"])

    return pa.table(out, schema=_SCHEMA)

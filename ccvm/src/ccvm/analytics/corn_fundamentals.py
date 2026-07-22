"""Corn-specific crop-condition, progress, yield, and production features."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

import pyarrow as pa

_SCHEMA = pa.schema([
    pa.field("as_of_date", pa.string()), pa.field("report_period", pa.string()),
    pa.field("condition_good_excellent_pct", pa.float64()),
    pa.field("condition_wow_pp", pa.float64()),
    pa.field("active_progress_json", pa.string()),
    pa.field("yield_bu_per_acre", pa.float64()),
    pa.field("production_million_bushels", pa.float64()),
    pa.field("supply_signal", pa.string()), pa.field("scenario_trigger", pa.string()),
    pa.field("observation_count", pa.int32()), pa.field("source_url", pa.string()),
])


def _latest(rows: list[dict], *terms: str) -> dict | None:
    matches = [row for row in rows if all(term in row["label"].upper() for term in terms)]
    return max(matches, key=lambda row: row["period"]) if matches else None


def _condition_by_period(rows: list[dict]) -> dict[str, float]:
    values: dict[str, float] = defaultdict(float)
    for row in rows:
        label = row["label"].upper()
        if "CONDITION" in label and "MEASURED IN PCT" in label \
                and (" GOOD" in label or " EXCELLENT" in label):
            values[row["period"]] += row["value"]
    return dict(values)


def compute(silver: pa.Table, as_of: date) -> pa.Table:
    data = silver.to_pydict()
    rows = [
        {key: data[key][i] for key in data}
        for i in range(len(silver)) if data["silver_status"][i] != "FAIL"
    ]
    conditions = _condition_by_period(rows)
    condition_periods = sorted(conditions)
    condition = conditions[condition_periods[-1]] if condition_periods else None
    condition_wow = (
        condition - conditions[condition_periods[-2]] if len(condition_periods) >= 2 else None
    )
    dated_periods = [
        row["period"] for row in rows
        if len(row["period"]) >= 10 and row["period"][4:5] == "-"
        and row["period"][5:7].isdigit()
    ]
    latest_period = max(dated_periods, default=as_of.isoformat())
    progress_rows = [row for row in rows if row["statistic_category"].upper() == "PROGRESS"]
    progress_period = max((row["period"] for row in progress_rows), default=None)
    progress = {}
    for row in progress_rows:
        if row["period"] == progress_period:
            progress[row["label"]] = row["value"]
    yield_row = _latest(rows, "YIELD", "BU / ACRE")
    production_row = _latest(rows, "PRODUCTION", "MEASURED IN BU")
    if condition_wow is not None and condition_wow <= -2.0:
        supply_signal, trigger = "draw", "bull_watch"
    elif condition_wow is not None and condition_wow >= 2.0:
        supply_signal, trigger = "build", "bear_watch"
    else:
        supply_signal, trigger = "neutral", "none"
    production = production_row["value"] / 1_000_000 if production_row else None
    return pa.table({
        "as_of_date": [as_of.isoformat()], "report_period": [latest_period],
        "condition_good_excellent_pct": [condition], "condition_wow_pp": [condition_wow],
        "active_progress_json": [json.dumps(progress, sort_keys=True)],
        "yield_bu_per_acre": [yield_row["value"] if yield_row else None],
        "production_million_bushels": [production], "supply_signal": [supply_signal],
        "scenario_trigger": [trigger], "observation_count": [len(rows)],
        "source_url": ["https://quickstats.nass.usda.gov/"],
    }, schema=_SCHEMA)


def report_section(table: pa.Table) -> dict:
    data = table.to_pydict()
    return {
        "status": "available", "provider": "USDA NASS Quick Stats",
        "report_period": data["report_period"][0],
        "condition_good_excellent_pct": data["condition_good_excellent_pct"][0],
        "condition_wow_pp": data["condition_wow_pp"][0],
        "active_progress": json.loads(data["active_progress_json"][0] or "{}"),
        "yield_bu_per_acre": data["yield_bu_per_acre"][0],
        "production_million_bushels": data["production_million_bushels"][0],
        "supply_signal": data["supply_signal"][0],
        "scenario_trigger": data["scenario_trigger"][0],
        "observation_count": data["observation_count"][0],
        "source_url": data["source_url"][0],
        "interpretation_note": (
            "Condition changes are descriptive supply evidence; they are not a yield forecast."
        ),
    }

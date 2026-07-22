"""Normalize parsed USDA NASS observations and retain explicit quality status."""
from __future__ import annotations

from datetime import date

import pyarrow as pa

_SCHEMA = pa.schema([
    pa.field("period", pa.string()), pa.field("series_id", pa.string()),
    pa.field("label", pa.string()), pa.field("statistic_category", pa.string()),
    pa.field("reference_period", pa.string()), pa.field("year", pa.int32()),
    pa.field("unit", pa.string()), pa.field("value", pa.float64()),
    pa.field("source_id", pa.string()), pa.field("raw_file_sha256", pa.string()),
    pa.field("silver_status", pa.string()), pa.field("silver_note", pa.string()),
])


def normalize(bronze: pa.Table, as_of: date) -> pa.Table:
    data = bronze.to_pydict()
    rows = {field.name: [] for field in _SCHEMA}
    for index, value in enumerate(data.get("value", [])):
        period = data["period"][index]
        status, note = "PASS", ""
        if value is None:
            status, note = "FAIL", "missing_value"
        elif len(period) >= 10 and period[:10] > as_of.isoformat():
            status, note = "FAIL", "future_observation"
        for key in data:
            rows[key].append(data[key][index])
        rows["silver_status"].append(status)
        rows["silver_note"].append(note)
    return pa.table(rows, schema=_SCHEMA)

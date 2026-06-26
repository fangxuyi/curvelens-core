"""Parse raw EIA API v2 JSON into bronze fundamental observations."""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

_SCHEMA = pa.schema([
    pa.field("period", pa.string()),          # YYYY-MM-DD (weekly period end)
    pa.field("series_id", pa.string()),
    pa.field("series_description", pa.string()),
    pa.field("value", pa.float64()),
    pa.field("units", pa.string()),
    pa.field("geography", pa.string()),
    pa.field("product", pa.string()),
    pa.field("source_id", pa.string()),
    pa.field("raw_file_sha256", pa.string()),
])


def parse(raw_path: Path, sha256: str) -> pa.Table:
    data = json.loads(raw_path.read_bytes())
    # EIA v2 response: {"response": {"data": [...], ...}}
    items = (
        data.get("response", {}).get("data")
        or data.get("data")
        or []
    )

    rows: dict[str, list] = {f.name: [] for f in _SCHEMA}

    for item in items:
        val = item.get("value")
        try:
            fval = float(val) if val is not None else None
        except (TypeError, ValueError):
            fval = None

        rows["period"].append(str(item.get("period", "")))
        rows["series_id"].append(str(item.get("series", item.get("series-id", ""))))
        rows["series_description"].append(
            str(item.get("series-description", item.get("seriesDescription", "")))
        )
        rows["value"].append(fval)
        rows["units"].append(str(item.get("units", "")))
        rows["geography"].append(str(item.get("duoarea", item.get("area-name", ""))))
        rows["product"].append(str(item.get("product", "")))
        rows["source_id"].append("eia_api_v2")
        rows["raw_file_sha256"].append(sha256)

    return pa.table(rows, schema=_SCHEMA)

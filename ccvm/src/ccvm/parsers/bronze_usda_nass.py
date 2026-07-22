"""Parse USDA NASS Quick Stats Corn observations into a stable bronze table."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow as pa

_SCHEMA = pa.schema([
    pa.field("period", pa.string()), pa.field("series_id", pa.string()),
    pa.field("label", pa.string()), pa.field("statistic_category", pa.string()),
    pa.field("reference_period", pa.string()), pa.field("year", pa.int32()),
    pa.field("unit", pa.string()), pa.field("value", pa.float64()),
    pa.field("source_id", pa.string()), pa.field("raw_file_sha256", pa.string()),
])


def _number(value) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text.startswith("(") or text in {"D", "NA", "Z"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _series_id(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def parse(raw_path: Path, sha256: str) -> pa.Table:
    payload = json.loads(raw_path.read_text())
    rows = {field.name: [] for field in _SCHEMA}
    for item in payload.get("data", []):
        value = _number(item.get("Value"))
        label = str(item.get("short_desc") or "").strip()
        if value is None or not label:
            continue
        year = int(item.get("year") or 0)
        period = str(item.get("week_ending") or "").strip()
        if not period:
            period = f"{year:04d}-{str(item.get('reference_period_desc') or 'ANNUAL').upper()}"
        values = {
            "period": period, "series_id": _series_id(label), "label": label,
            "statistic_category": str(item.get("statisticcat_desc") or ""),
            "reference_period": str(item.get("reference_period_desc") or ""),
            "year": year, "unit": str(item.get("unit_desc") or ""), "value": value,
            "source_id": "usda_nass_corn", "raw_file_sha256": sha256,
        }
        for key, field_value in values.items():
            rows[key].append(field_value)
    return pa.table(rows, schema=_SCHEMA)

"""Parse raw yfinance_wti_futures JSON into a bronze PyArrow table."""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

_SCHEMA = pa.schema([
    pa.field("trade_date", pa.string()),
    pa.field("exchange", pa.string()),
    pa.field("product", pa.string()),
    pa.field("contract_code", pa.string()),
    pa.field("delivery_month", pa.string()),
    pa.field("settlement", pa.float64()),
    pa.field("volume", pa.int64()),
    pa.field("open_interest", pa.int64()),
    pa.field("currency", pa.string()),
    pa.field("price_unit", pa.string()),
    pa.field("source_id", pa.string()),
    pa.field("raw_file_sha256", pa.string()),
])


def parse(raw_path: Path, sha256: str) -> pa.Table:
    """
    Parse a single raw yfinance futures JSON file into bronze.
    sha256 is the hex digest of raw_path's bytes (stored in manifest).
    """
    data = json.loads(raw_path.read_bytes())
    settlements = data.get("settlements", [])

    rows: dict[str, list] = {f.name: [] for f in _SCHEMA}

    for r in settlements:
        rows["trade_date"].append(str(r.get("trade_date", "")))
        rows["exchange"].append(str(r.get("exchange", "NYMEX")))
        rows["product"].append(str(r.get("product", "CL")))
        rows["contract_code"].append(str(r.get("contract_code", "")))
        rows["delivery_month"].append(str(r.get("delivery_month", "")))
        v = r.get("settlement")
        rows["settlement"].append(float(v) if v is not None else None)
        vol = r.get("volume")
        rows["volume"].append(int(vol) if vol is not None else None)
        oi = r.get("open_interest")
        rows["open_interest"].append(int(oi) if oi is not None else None)
        rows["currency"].append(str(r.get("currency", "USD")))
        rows["price_unit"].append(str(r.get("price_unit", "USD/BBL")))
        rows["source_id"].append(str(r.get("source_id", "")))
        rows["raw_file_sha256"].append(sha256)

    return pa.table(rows, schema=_SCHEMA)

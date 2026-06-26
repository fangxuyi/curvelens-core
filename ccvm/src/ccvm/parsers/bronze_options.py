"""Parse raw options JSON (etrade_uso_options or barchart_wti_options) into bronze."""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

_SCHEMA = pa.schema([
    pa.field("trade_date", pa.string()),
    pa.field("option_expiry", pa.string()),
    pa.field("option_symbol", pa.string()),
    pa.field("underlying_contract", pa.string()),
    pa.field("underlying_delivery_month", pa.string()),
    pa.field("strike", pa.float64()),
    pa.field("call_put", pa.string()),
    pa.field("settlement", pa.float64()),
    pa.field("bid", pa.float64()),
    pa.field("ask", pa.float64()),
    pa.field("volume", pa.int64()),
    pa.field("open_interest", pa.int64()),
    pa.field("implied_volatility", pa.float64()),
    pa.field("delta", pa.float64()),
    pa.field("gamma", pa.float64()),
    pa.field("theta", pa.float64()),
    pa.field("vega", pa.float64()),
    pa.field("exercise_style", pa.string()),
    pa.field("settlement_style", pa.string()),
    pa.field("contract_multiplier", pa.int64()),
    pa.field("source_id", pa.string()),
    pa.field("price_note", pa.string()),
    pa.field("raw_file_sha256", pa.string()),
])


def _opt_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _opt_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse(raw_path: Path, sha256: str) -> pa.Table:
    data = json.loads(raw_path.read_bytes())
    settlements = data.get("settlements", [])

    rows: dict[str, list] = {f.name: [] for f in _SCHEMA}

    for r in settlements:
        rows["trade_date"].append(str(r.get("trade_date", "")))
        rows["option_expiry"].append(str(r.get("option_expiry", "")))
        rows["option_symbol"].append(str(r.get("option_symbol", "") or ""))
        rows["underlying_contract"].append(str(r.get("underlying_contract", "")))
        rows["underlying_delivery_month"].append(str(r.get("underlying_delivery_month", "")))
        rows["strike"].append(_opt_float(r.get("strike")))
        rows["call_put"].append(str(r.get("call_put", "")))
        rows["settlement"].append(_opt_float(r.get("settlement")))
        rows["bid"].append(_opt_float(r.get("bid")))
        rows["ask"].append(_opt_float(r.get("ask")))
        rows["volume"].append(_opt_int(r.get("volume")))
        rows["open_interest"].append(_opt_int(r.get("open_interest")))
        rows["implied_volatility"].append(_opt_float(r.get("implied_volatility")))
        rows["delta"].append(_opt_float(r.get("delta")))
        rows["gamma"].append(_opt_float(r.get("gamma")))
        rows["theta"].append(_opt_float(r.get("theta")))
        rows["vega"].append(_opt_float(r.get("vega")))
        rows["exercise_style"].append(str(r.get("exercise_style", "")))
        rows["settlement_style"].append(str(r.get("settlement_style", "")))
        rows["contract_multiplier"].append(_opt_int(r.get("contract_multiplier")))
        rows["source_id"].append(str(r.get("source_id", "")))
        rows["price_note"].append(str(r.get("price_note", "") or ""))
        rows["raw_file_sha256"].append(sha256)

    return pa.table(rows, schema=_SCHEMA)

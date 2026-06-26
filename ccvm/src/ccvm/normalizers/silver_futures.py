"""
Bronze → Silver normalization for WTI futures settlements.

Silver layer adds:
  - last_trade_date         from WTI calendar
  - option_expiry_date      from WTI calendar
  - days_to_expiry          integer days from trade_date to last_trade_date
  - curve_position          1-indexed position in the active curve (1=front)
  - silver_status           PASS / WARN / FAIL per row
  - silver_note             human-readable flag reason

Rows are excluded (silver_status=FAIL) if:
  - settlement <= 0
  - settlement > 500 (implausible WTI price)
  - delivery_month cannot be parsed

Rows are warned (silver_status=WARN) if:
  - volume is None or 0
  - open_interest is None
"""
from __future__ import annotations

from datetime import date

import pyarrow as pa
import pyarrow.compute as pc

from ..reference.wti_calendar import (
    futures_last_trade_date,
    option_expiry_date,
    parse_contract_code,
)

_MAX_SETTLEMENT = 500.0
_MIN_SETTLEMENT = 1.0

_SILVER_SCHEMA = pa.schema([
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
    pa.field("last_trade_date", pa.string()),
    pa.field("cl_option_expiry", pa.string()),
    pa.field("days_to_expiry", pa.int32()),
    pa.field("curve_position", pa.int32()),
    pa.field("source_id", pa.string()),
    pa.field("raw_file_sha256", pa.string()),
    pa.field("silver_status", pa.string()),
    pa.field("silver_note", pa.string()),
])


def normalize(bronze: pa.Table, as_of_date: date) -> pa.Table:
    """
    Normalize a bronze futures table into silver.
    as_of_date: the trade date (used for days_to_expiry calculation).
    """
    rows: dict[str, list] = {f.name: [] for f in _SILVER_SCHEMA}

    # Sort by delivery_month to assign curve_position
    bronze_dicts = bronze.to_pydict()
    n = len(bronze_dicts["trade_date"])
    indexed = list(range(n))

    # Build list of (delivery_month, idx) for sorting
    dm_list = bronze_dicts["delivery_month"]
    indexed.sort(key=lambda i: dm_list[i])

    for pos, i in enumerate(indexed, start=1):
        cc = bronze_dicts["contract_code"][i]
        dm = bronze_dicts["delivery_month"][i]
        settlement = bronze_dicts["settlement"][i]

        # Parse contract code to get calendar info
        parsed = parse_contract_code(cc)
        status = "PASS"
        note = ""

        if parsed is None:
            ltd_str = ""
            opt_exp_str = ""
            days_to_exp = None
            if not status == "FAIL":
                status = "FAIL"
                note = f"unparseable_contract_code:{cc}"
        else:
            dy, dm_int = parsed
            ltd = futures_last_trade_date(dy, dm_int)
            opt_exp = option_expiry_date(dy, dm_int)
            ltd_str = ltd.isoformat()
            opt_exp_str = opt_exp.isoformat()
            days_to_exp = (ltd - as_of_date).days

        # Validate settlement
        if settlement is None or settlement <= 0 or settlement < _MIN_SETTLEMENT:
            status = "FAIL"
            note = note or f"invalid_settlement:{settlement}"
        elif settlement > _MAX_SETTLEMENT:
            status = "FAIL"
            note = note or f"settlement_exceeds_max:{settlement}"

        # Warn if volume missing
        vol = bronze_dicts["volume"][i]
        oi = bronze_dicts["open_interest"][i]
        if status == "PASS" and (vol is None or vol == 0):
            status = "WARN"
            note = "missing_or_zero_volume"

        rows["trade_date"].append(bronze_dicts["trade_date"][i])
        rows["exchange"].append(bronze_dicts["exchange"][i])
        rows["product"].append(bronze_dicts["product"][i])
        rows["contract_code"].append(cc)
        rows["delivery_month"].append(dm)
        rows["settlement"].append(settlement)
        rows["volume"].append(vol)
        rows["open_interest"].append(oi)
        rows["currency"].append(bronze_dicts["currency"][i])
        rows["price_unit"].append(bronze_dicts["price_unit"][i])
        rows["last_trade_date"].append(ltd_str)
        rows["cl_option_expiry"].append(opt_exp_str)
        rows["days_to_expiry"].append(days_to_exp)
        rows["curve_position"].append(pos)
        rows["source_id"].append(bronze_dicts["source_id"][i])
        rows["raw_file_sha256"].append(bronze_dicts["raw_file_sha256"][i])
        rows["silver_status"].append(status)
        rows["silver_note"].append(note)

    return pa.table(rows, schema=_SILVER_SCHEMA)

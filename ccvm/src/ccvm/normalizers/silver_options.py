"""
Bronze → Silver normalization for product option settlements.

Silver layer:
  - Validates strike > 0 and settlement >= 0
  - Validates option_expiry > trade_date
  - Flags sparse strike coverage per (expiry, underlying, call_put)
  - Tags silver_status (PASS / WARN / FAIL)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

import pyarrow as pa

from ..reference.product import get_product

_SILVER_SCHEMA = pa.schema([
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
    pa.field("silver_status", pa.string()),
    pa.field("silver_note", pa.string()),
])


def normalize(bronze: pa.Table, as_of_date: date) -> pa.Table:
    d = bronze.to_pydict()
    n = len(d["trade_date"])
    product = get_product()

    # A settlement key must identify exactly one instrument. Duplicate keys
    # usually mean another bulletin product leaked into the selected section;
    # choosing one by row order would silently corrupt surfaces and RNDs.
    identity_keys = [
        (d["option_expiry"][i], d["underlying_contract"][i],
         d["call_put"][i], d["strike"][i])
        for i in range(n)
    ]
    identity_counts = Counter(identity_keys)

    # Pre-compute per-expiry strike counts for coverage check
    strike_sets: dict[tuple, set] = defaultdict(set)
    for i in range(n):
        key = (d["option_expiry"][i], d["underlying_contract"][i], d["call_put"][i])
        s = d["strike"][i]
        if s is not None and s > 0:
            strike_sets[key].add(s)

    rows: dict[str, list] = {f.name: [] for f in _SILVER_SCHEMA}

    for i in range(n):
        exp_str = d["option_expiry"][i]
        try:
            exp_date = date.fromisoformat(exp_str)
        except (ValueError, TypeError):
            exp_date = None

        strike = d["strike"][i]
        settlement = d["settlement"][i]
        cp = d["call_put"][i]

        status = "PASS"
        note = ""

        # Hard failures
        identity = (exp_str, d["underlying_contract"][i], cp, strike)
        if identity_counts[identity] > 1:
            status = "FAIL"
            note = f"duplicate_contract_key:{identity_counts[identity]}_rows"
        elif exp_date is None or exp_date <= as_of_date:
            status = "FAIL"
            note = f"option_expiry_not_after_trade_date:{exp_str}"
        elif strike is None or strike <= 0:
            status = "FAIL"
            note = f"invalid_strike:{strike}"
        elif settlement is None or settlement < 0:
            status = "FAIL"
            note = f"negative_settlement:{settlement}"
        else:
            # Coverage warning
            key = (exp_str, d["underlying_contract"][i], cp)
            n_strikes = len(strike_sets[key])
            if n_strikes < product.fail_strikes_below:
                status = "FAIL"
                note = f"critically_sparse:{n_strikes}_strikes"
            elif n_strikes < product.pass_strikes_at:
                status = "WARN"
                note = f"sparse_strikes:{n_strikes}"

        rows["trade_date"].append(d["trade_date"][i])
        rows["option_expiry"].append(exp_str)
        rows["option_symbol"].append(d["option_symbol"][i])
        rows["underlying_contract"].append(d["underlying_contract"][i])
        rows["underlying_delivery_month"].append(d["underlying_delivery_month"][i])
        rows["strike"].append(strike)
        rows["call_put"].append(cp)
        rows["settlement"].append(settlement)
        rows["bid"].append(d["bid"][i])
        rows["ask"].append(d["ask"][i])
        rows["volume"].append(d["volume"][i])
        rows["open_interest"].append(d["open_interest"][i])
        rows["implied_volatility"].append(d["implied_volatility"][i])
        rows["delta"].append(d["delta"][i])
        rows["gamma"].append(d["gamma"][i])
        rows["theta"].append(d["theta"][i])
        rows["vega"].append(d["vega"][i])
        rows["exercise_style"].append(d["exercise_style"][i])
        rows["settlement_style"].append(d["settlement_style"][i])
        rows["contract_multiplier"].append(d["contract_multiplier"][i])
        rows["source_id"].append(d["source_id"][i])
        rows["price_note"].append(d["price_note"][i])
        rows["raw_file_sha256"].append(d["raw_file_sha256"][i])
        rows["silver_status"].append(status)
        rows["silver_note"].append(note)

    return pa.table(rows, schema=_SILVER_SCHEMA)

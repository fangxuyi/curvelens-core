"""Surface identity and constant-contract daily comparisons (IVs are decimals)."""
from __future__ import annotations

import math

SURFACE_METRICS = ("atm_iv", "risk_reversal_25d", "butterfly_25d", "skew_slope")


def front_surface(table):
    """Select one surface deterministically; never fill holes from another expiry."""
    if table is None:
        return {}
    rows = table.to_pylist()
    rows = [r for r in rows if r.get("option_expiry") and r.get("underlying_contract")]
    if not rows:
        return {}
    key = min((r["option_expiry"], r["underlying_contract"]) for r in rows)
    matches = [r for r in rows if (r["option_expiry"], r["underlying_contract"]) == key]
    result = dict(zip(("option_expiry", "underlying_contract"), key))
    for metric in SURFACE_METRICS:
        values = {r.get(metric) for r in matches}
        # Repeated strike rows must agree on their shared surface summary.
        value = next(iter(values)) if len(values) == 1 else None
        result[metric] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
    result["coverage_note"] = matches[0].get("price_note")
    return result


def compare_front_surface(today_table, prior_table, today_date, prior_date):
    today = front_surface(today_table)
    prior_front = front_surface(prior_table)
    key = (today.get("option_expiry"), today.get("underlying_contract"))
    rows = prior_table.to_pylist() if prior_table is not None else []
    matches = [r for r in rows if (r.get("option_expiry"), r.get("underlying_contract")) == key]
    # Reuse the same consistency/finite-value checks on the matched surface.
    if matches:
        import pyarrow as pa
        previous = front_surface(pa.Table.from_pylist(matches))
    else:
        previous = {}
    out = {
        "basis": "same_expiry_and_underlying", "trade_date": today_date,
        "prior_date": prior_date, "option_expiry": key[0], "underlying_contract": key[1],
        "prior_headline_expiry": prior_front.get("option_expiry"),
        "prior_headline_underlying": prior_front.get("underlying_contract"),
        "headline_surface_changed": bool(today and prior_front and key != (
            prior_front.get("option_expiry"), prior_front.get("underlying_contract"))),
        "status": "available" if today.get("atm_iv") is not None and previous.get("atm_iv") is not None else "unavailable",
    }
    for metric in SURFACE_METRICS:
        a, b = today.get(metric), previous.get(metric)
        out[metric + "_today"] = a
        out[metric + "_prior"] = b
        out[metric + "_change"] = a - b if a is not None and b is not None else None
    change = out["atm_iv_change"]
    out["atm_iv_change_pp"] = change * 100 if change is not None else None
    out["direction"] = ("increased" if change > 0 else "declined" if change < 0 else "unchanged") if change is not None else "unavailable"
    if out["status"] == "available":
        out["comparison_text"] = (
            f"{key[0]} / {key[1]}: ATM IV {out['atm_iv_prior'] * 100:.6f}% "
            f"on {prior_date} to {out['atm_iv_today'] * 100:.6f}% on {today_date}; "
            f"{out['atm_iv_change_pp']:+.6f} percentage points ({out['direction']}); "
            "same expiry and underlying."
        )
    else:
        out["comparison_text"] = "Same-expiry and same-underlying prior ATM IV unavailable; no daily direction established."
    return out

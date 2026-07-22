"""Normalize and interpret profile-configured macro observations."""
from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

import pyarrow as pa

from ..reference.product import MacroSeriesSpec, get_product
from .history_context import percentile_of

_SCHEMA = pa.schema([
    pa.field("as_of_date", pa.string()),
    pa.field("series_key", pa.string()),
    pa.field("series_id", pa.string()),
    pa.field("label", pa.string()),
    pa.field("units", pa.string()),
    pa.field("role", pa.string()),
    pa.field("flat_price_sign", pa.int8()),
    pa.field("observation_date", pa.string()),
    pa.field("value", pa.float64()),
    pa.field("source_id", pa.string()),
    pa.field("raw_sha256", pa.string()),
])


def normalize_fred(raw_path: Path, sha256: str, series: MacroSeriesSpec,
                   as_of_date: date) -> pa.Table:
    """Parse one immutable FRED response, excluding missing/future values."""
    payload = json.loads(raw_path.read_text())
    rows = {field.name: [] for field in _SCHEMA}
    for obs in payload.get("observations", []):
        obs_date = obs.get("date")
        raw_value = obs.get("value")
        if not obs_date or obs_date > as_of_date.isoformat() or raw_value in (None, "."):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        values = {
            "as_of_date": as_of_date.isoformat(), "series_key": series.key,
            "series_id": series.series_id, "label": series.label,
            "units": series.units, "role": series.role,
            "flat_price_sign": series.flat_price_sign,
            "observation_date": obs_date, "value": value,
            "source_id": f"fred_{get_product().key}_macro_{series.key}",
            "raw_sha256": sha256,
        }
        for key, value in values.items():
            rows[key].append(value)
    return pa.table(rows, schema=_SCHEMA)


def _latest_two(rows: list[tuple[str, float]]) -> tuple[tuple[str, float] | None,
                                                        tuple[str, float] | None]:
    ordered = sorted(rows)
    return (ordered[-1] if ordered else None,
            ordered[-2] if len(ordered) >= 2 else None)


def compute(silver_macro: pa.Table, gold_futures: pa.Table,
            gold_options: pa.Table | None = None) -> dict:
    """Build descriptive macro, flat-price, carry, and vol-surface context.

    Signs are profile priors, not a fitted forecast. The output deliberately
    separates observed facts from interpretation and records freshness.
    """
    d = silver_macro.to_pydict()
    grouped: dict[str, list[tuple[str, float]]] = {}
    meta: dict[str, dict] = {}
    for i, key in enumerate(d["series_key"]):
        grouped.setdefault(key, []).append((d["observation_date"][i], d["value"][i]))
        meta[key] = {name: d[name][i] for name in
                     ("series_id", "label", "units", "role", "flat_price_sign")}

    series_out: dict[str, dict] = {}
    score = 0
    scored = 0
    impulse_parts: list[float] = []
    for key, rows in grouped.items():
        latest, prior = _latest_two(rows)
        if latest is None:
            continue
        change = latest[1] - prior[1] if prior else None
        change_pct = ((latest[1] / prior[1]) - 1) if prior and prior[1] else None
        sign = int(meta[key]["flat_price_sign"])
        history_values = [value for _, value in rows]
        # Yield series are interpreted in basis points; indices in percent.
        impulse = change * 100 if meta[key]["units"] == "percent" else (
            change_pct * 100 if change_pct is not None else None)
        if sign and impulse is not None and impulse != 0:
            score += sign * (1 if impulse > 0 else -1)
            scored += 1
            impulse_parts.append(abs(impulse))
        series_out[key] = {
            **meta[key], "observation_date": latest[0], "value": latest[1],
            "prior_observation_date": prior[0] if prior else None,
            "change": change, "change_pct": change_pct,
            "change_display_unit": "bp" if meta[key]["units"] == "percent" else "percent",
            "signed_flat_price_impulse": sign * impulse if impulse is not None else None,
            "history_observations": len(history_values),
            "history_percentile": percentile_of(history_values, latest[1]),
            "history_median": statistics.median(history_values),
            "history_low": min(history_values),
            "history_high": max(history_values),
            "source_url": f"https://fred.stlouisfed.org/series/{meta[key]['series_id']}",
        }

    if not scored or score == 0:
        direction = "mixed_or_neutral"
    else:
        direction = "supportive" if score > 0 else "headwind"

    fd = gold_futures.to_pydict()
    settles = fd.get("settlement", [])
    annualized_roll = None
    implied_carry = None
    if len(settles) >= 2 and settles[0] and settles[1]:
        annualized_roll = (settles[0] - settles[1]) / settles[0] * 12
        implied_carry = -annualized_roll
    short_rate = series_out.get("treasury_3m", {}).get("value")
    carry_gap = implied_carry - short_rate / 100 if implied_carry is not None and short_rate is not None else None

    vol: dict = {"status": "unavailable"}
    if gold_options is not None and len(gold_options):
        od = gold_options.to_pydict()
        atm = next((v for v in od.get("atm_iv", []) if v is not None), None)
        rr = next((v for v in od.get("risk_reversal_25d", []) if v is not None), None)
        bf = next((v for v in od.get("butterfly_25d", []) if v is not None), None)
        vol = {"status": "available", "atm_iv": atm, "risk_reversal_25d": rr,
               "butterfly_25d": bf,
               "interpretation": "call_skew" if rr is not None and rr > 0 else
                                 "put_skew" if rr is not None and rr < 0 else "balanced_skew"}

    return {
        "status": "available" if series_out else "unavailable",
        "as_of_date": d["as_of_date"][0] if d.get("as_of_date") else None,
        "series": series_out,
        "flat_price": {
            "directional_prior": direction, "score": score,
            "signals_scored": scored,
            "method": "sum of configured signs applied to latest daily changes; descriptive, not fitted",
        },
        "curve": {
            "annualized_roll_yield": annualized_roll,
            "implied_carry": implied_carry, "treasury_3m": short_rate,
            "carry_gap": carry_gap,
            "interpretation": "gap may reflect financing, storage, lease rates, convenience yield, and liquidity; not an arbitrage claim",
        },
        "vol_surface": vol,
        "history": {
            "observation_rows": len(silver_macro),
            "market_response_status": "requires multiple accumulated CurveLens trade dates",
        },
        "caveat": "Macro relationships are conditional and can be overwhelmed by risk demand, official-sector flows, and positioning.",
    }

"""
EIA gold-layer features.

Collapses the latest-period headline series from silver into one summary
row per trade_date. Computes WoW draw/build signals and a supply_signal
that feeds into the agreement classifier and daily report.

Columns:
  Stocks (MBBL)
    crude_stocks_ex_spr     U.S. crude ex-SPR
    spr_stocks              SPR level
    cushing_stocks          Cushing, OK level
    gasoline_stocks         total gasoline
    distillate_stocks       distillate fuel oil

  Flows (MBBL/D)
    crude_imports           weekly crude imports
    crude_exports           weekly crude exports
    net_imports             imports - exports

  Refinery
    refinery_utilization_pct   % operable capacity used

  WoW draws (MBBL, positive = draw = bullish, negative = build = bearish)
    crude_draw              -(wow_change of us_crude_ex_spr)
    cushing_draw            -(wow_change of cushing_stocks)
    gasoline_draw           -(wow_change of gasoline_stocks)
    distillate_draw         -(wow_change of distillate_stocks)

  Signals
    supply_signal           "draw" / "build" / "neutral" (based on crude_draw)
    supply_magnitude_mbbl   abs(crude_draw)
    cushing_signal          "draw" / "build" / "neutral"
    scenario_trigger        "bull_confirmed" / "bear_watch" / "bear_confirmed" / "none"

Threshold constants match the scenario engine's confirmation / invalidation
trigger language so both are consistent.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pyarrow as pa

# Draw/build thresholds (MBBL)
_BULL_DRAW_THRESHOLD  =  3_000   # > 3M bbl draw  → bull trigger confirmed
_BEAR_WATCH_THRESHOLD = -2_000   # < -2M bbl (build) → bear watch
_BEAR_CONFIRMED_THRESHOLD = -4_000  # < -4M bbl build → bear confirmed

# Refinery utilization: high = demand pulling crude, tight supply
_HIGH_UTIL = 92.0
_LOW_UTIL  = 85.0

_SCHEMA = pa.schema([
    pa.field("trade_date",               pa.string()),
    pa.field("eia_period",               pa.string()),   # EIA week-end date
    # Stocks (MBBL)
    pa.field("crude_stocks_ex_spr",      pa.float64()),
    pa.field("spr_stocks",               pa.float64()),
    pa.field("cushing_stocks",           pa.float64()),
    pa.field("gasoline_stocks",          pa.float64()),
    pa.field("distillate_stocks",        pa.float64()),
    # Flows (MBBL/D)
    pa.field("crude_imports",            pa.float64()),
    pa.field("crude_exports",            pa.float64()),
    pa.field("net_imports",              pa.float64()),
    # Refinery
    pa.field("refinery_utilization_pct", pa.float64()),
    # WoW draws (positive = draw = bullish)
    pa.field("crude_draw",               pa.float64()),
    pa.field("cushing_draw",             pa.float64()),
    pa.field("gasoline_draw",            pa.float64()),
    pa.field("distillate_draw",          pa.float64()),
    # Signals
    pa.field("supply_signal",            pa.string()),
    pa.field("supply_magnitude_mbbl",    pa.float64()),
    pa.field("cushing_signal",           pa.string()),
    pa.field("scenario_trigger",         pa.string()),
    pa.field("source_id",                pa.string()),
])


def compute(silver_eia: pa.Table, as_of_date: date) -> pa.Table:
    """
    Compute EIA gold features from a silver EIA table.
    Returns a single-row table for the trade_date.
    """
    if silver_eia is None or len(silver_eia) == 0:
        return _empty(as_of_date)

    d = silver_eia.to_pydict()
    n = len(d["trade_date"])

    # Index latest-period headline rows by series_key
    latest: dict[str, dict] = {}
    for i in range(n):
        if not d["is_latest_period"][i]:
            continue
        key = d["series_key"][i]
        latest[key] = {col: d[col][i] for col in d}

    if not latest:
        return _empty(as_of_date)

    def val(key: str) -> Optional[float]:
        row = latest.get(key)
        return row["value"] if row else None

    def wow(key: str) -> Optional[float]:
        row = latest.get(key)
        return row["wow_change"] if row else None

    def period(key: str) -> Optional[str]:
        row = latest.get(key)
        return row["period"] if row else None

    # Determine the EIA period from any headline series
    eia_period = (
        period("us_crude_ex_spr")
        or period("cushing_stocks")
        or period("gasoline_stocks")
        or ""
    )

    crude_stocks  = val("us_crude_ex_spr")
    spr_stocks    = val("us_spr_stocks")
    cush_stocks   = val("cushing_stocks")
    gas_stocks    = val("gasoline_stocks")
    dist_stocks   = val("distillate_stocks")
    imports       = val("us_crude_imports")
    exports       = val("us_crude_exports")
    refinery_util = val("refinery_utilization_pct")

    net_imports = (
        (imports - exports) if (imports is not None and exports is not None) else None
    )

    # WoW changes: EIA reports stock levels, so draw = stocks fell = wow_change < 0
    # We flip the sign so positive crude_draw means bullish (stocks decreased)
    crude_wow  = wow("us_crude_ex_spr")
    cush_wow   = wow("cushing_stocks")
    gas_wow    = wow("gasoline_stocks")
    dist_wow   = wow("distillate_stocks")

    crude_draw = (-crude_wow) if crude_wow is not None else None
    cush_draw  = (-cush_wow)  if cush_wow  is not None else None
    gas_draw   = (-gas_wow)   if gas_wow   is not None else None
    dist_draw  = (-dist_wow)  if dist_wow  is not None else None

    # Supply signal from crude draw
    supply_signal, supply_magnitude = _stock_signal(crude_draw)
    cushing_signal, _ = _stock_signal(cush_draw)

    # Scenario trigger
    scenario_trigger = _scenario_trigger(crude_draw, refinery_util)

    row: dict[str, list] = {f.name: [] for f in _SCHEMA}
    row["trade_date"].append(as_of_date.isoformat())
    row["eia_period"].append(eia_period)
    row["crude_stocks_ex_spr"].append(crude_stocks)
    row["spr_stocks"].append(spr_stocks)
    row["cushing_stocks"].append(cush_stocks)
    row["gasoline_stocks"].append(gas_stocks)
    row["distillate_stocks"].append(dist_stocks)
    row["crude_imports"].append(imports)
    row["crude_exports"].append(exports)
    row["net_imports"].append(net_imports)
    row["refinery_utilization_pct"].append(refinery_util)
    row["crude_draw"].append(crude_draw)
    row["cushing_draw"].append(cush_draw)
    row["gasoline_draw"].append(gas_draw)
    row["distillate_draw"].append(dist_draw)
    row["supply_signal"].append(supply_signal)
    row["supply_magnitude_mbbl"].append(supply_magnitude)
    row["cushing_signal"].append(cushing_signal)
    row["scenario_trigger"].append(scenario_trigger)
    row["source_id"].append("eia_api_v2")

    return pa.table(row, schema=_SCHEMA)


def _stock_signal(draw: Optional[float]) -> tuple[str, Optional[float]]:
    if draw is None:
        return "neutral", None
    if draw > 500:
        return "draw", abs(draw)
    if draw < -500:
        return "build", abs(draw)
    return "neutral", abs(draw)


def _scenario_trigger(
    crude_draw: Optional[float],
    refinery_util: Optional[float],
) -> str:
    if crude_draw is None:
        return "none"
    if crude_draw > _BULL_DRAW_THRESHOLD:
        return "bull_confirmed"
    if crude_draw < _BEAR_CONFIRMED_THRESHOLD:
        return "bear_confirmed"
    if crude_draw < _BEAR_WATCH_THRESHOLD:
        return "bear_watch"
    return "none"


def _empty(as_of_date: date) -> pa.Table:
    return pa.table(
        {f.name: [None] if f.name not in ("trade_date", "source_id", "supply_signal",
                                           "cushing_signal", "scenario_trigger", "eia_period")
                 else [""] for f in _SCHEMA},
        schema=_SCHEMA,
    )


def report_section(gold: pa.Table) -> dict:
    """Provider-owned mapping from gold features to the report payload."""
    if gold is None or len(gold) == 0:
        return {"status": "unavailable"}
    d = gold.to_pydict()
    return {
        "status": "available",
        "eia_period": d["eia_period"][0],
        "crude_stocks_ex_spr_mbbl": d["crude_stocks_ex_spr"][0],
        "spr_stocks_mbbl": d.get("spr_stocks", [None])[0],
        "cushing_stocks_mbbl": d["cushing_stocks"][0],
        "crude_draw_mbbl": d["crude_draw"][0],
        "cushing_draw_mbbl": d["cushing_draw"][0],
        "crude_imports_mbbld": d["crude_imports"][0],
        "crude_exports_mbbld": d["crude_exports"][0],
        "net_imports_mbbld": d["net_imports"][0],
        "refinery_utilization_pct": d["refinery_utilization_pct"][0],
        "gasoline_stocks_mbbl": d["gasoline_stocks"][0],
        "distillate_stocks_mbbl": d["distillate_stocks"][0],
        "gasoline_draw_mbbl": d["gasoline_draw"][0],
        "distillate_draw_mbbl": d["distillate_draw"][0],
        "supply_signal": d["supply_signal"][0],
        "cushing_signal": d["cushing_signal"][0],
        "scenario_trigger": d["scenario_trigger"][0],
    }

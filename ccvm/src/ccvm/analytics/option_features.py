"""
Options surface analytics (gold layer features).

Computed from silver options + silver futures for a single trade date.

Per-option features:
  - black76_iv             implied vol via Black-76 (European approximation)
  - black76_delta          model delta (if market delta missing)
  - black76_vega           model vega per 1% vol move
  - moneyness              log(F/K) — negative = OTM call / ITM put
  - time_to_expiry_years

Surface summaries per (expiry, underlying):
  - atm_iv                 IV at the strike closest to ATM
  - iv_25d_call            IV at 25-delta call strike (interpolated)
  - iv_25d_put             IV at 25-delta put strike (interpolated)
  - risk_reversal_25d      iv_25d_call - iv_25d_put
  - butterfly_25d          (iv_25d_call + iv_25d_put) / 2 - atm_iv
  - skew_slope             linear regression slope of IV vs log-moneyness
  - valid_call_strikes     count of PASS call rows
  - valid_put_strikes      count of PASS put rows
  - coverage_status        PASS / WARN / FAIL

The surface summary is denormalized onto every row for convenience.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

import pyarrow as pa

from .black76 import black76_greeks, implied_vol

_RISK_FREE_RATE = 0.05  # 5% — approximate; for USO/CL options, use T-bill rate

_SCHEMA = pa.schema([
    pa.field("trade_date", pa.string()),
    pa.field("option_expiry", pa.string()),
    pa.field("option_symbol", pa.string()),
    pa.field("underlying_contract", pa.string()),
    pa.field("strike", pa.float64()),
    pa.field("call_put", pa.string()),
    pa.field("settlement", pa.float64()),
    pa.field("forward_price", pa.float64()),        # underlying futures/ETF settle
    pa.field("time_to_expiry_years", pa.float64()),
    pa.field("moneyness_log", pa.float64()),        # log(F/K)
    pa.field("black76_iv", pa.float64()),
    pa.field("black76_delta", pa.float64()),
    pa.field("black76_vega", pa.float64()),
    pa.field("market_delta", pa.float64()),
    pa.field("atm_iv", pa.float64()),
    pa.field("iv_25d_call", pa.float64()),
    pa.field("iv_25d_put", pa.float64()),
    pa.field("risk_reversal_25d", pa.float64()),
    pa.field("butterfly_25d", pa.float64()),
    pa.field("skew_slope", pa.float64()),
    pa.field("valid_call_strikes", pa.int32()),
    pa.field("valid_put_strikes", pa.int32()),
    pa.field("coverage_status", pa.string()),
    pa.field("source_id", pa.string()),
    pa.field("price_note", pa.string()),
])


def _linear_regression_slope(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if var_x == 0:
        return None
    return cov / var_x


def _interpolate_iv_at_delta(
    strikes: list[float],
    ivs: list[float],
    deltas: list[float],
    target_abs_delta: float,
) -> Optional[float]:
    """Find IV at a target absolute delta (e.g., 0.25) by linear interpolation."""
    if len(strikes) < 2:
        return None
    # Sort by strike descending (higher strike = lower delta for calls)
    combined = sorted(zip(deltas, ivs), reverse=True)
    sorted_deltas = [x[0] for x in combined]
    sorted_ivs = [x[1] for x in combined]

    # Find bracketing pair
    for i in range(len(sorted_deltas) - 1):
        d0, d1 = sorted_deltas[i], sorted_deltas[i + 1]
        if d1 <= target_abs_delta <= d0:
            if d0 == d1:
                return sorted_ivs[i]
            t = (target_abs_delta - d1) / (d0 - d1)
            return sorted_ivs[i + 1] + t * (sorted_ivs[i] - sorted_ivs[i + 1])
    return None


def compute(
    silver_options: pa.Table,
    silver_futures: pa.Table,
    as_of_date: date,
) -> pa.Table:
    """
    Compute option surface features.
    silver_futures is used to get the forward price (settle of the front month
    or the matching underlying contract).
    """
    # Build forward price lookup: underlying_contract → settlement
    fu_d = silver_futures.to_pydict()
    fu_n = len(fu_d["trade_date"])
    forward_by_contract: dict[str, float] = {}
    for i in range(fu_n):
        if fu_d.get("silver_status", ["PASS"] * fu_n)[i] not in ("FAIL",):
            code = fu_d["contract_code"][i]
            s = fu_d["settlement"][i]
            if code and s:
                forward_by_contract[code] = s

    # Fallback forward: front-month settle when underlying contract not in the futures table
    front_settle: Optional[float] = None
    for pos_val, s_val in sorted(
        zip(fu_d.get("curve_position", [999] * fu_n), fu_d["settlement"]),
        key=lambda x: x[0],
    ):
        if s_val is not None:
            front_settle = s_val
            break

    od = silver_options.to_pydict()
    n = len(od["trade_date"])

    # Group by (expiry, underlying) → list of indices
    from collections import defaultdict
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i in range(n):
        if od.get("silver_status", ["PASS"] * n)[i] == "FAIL":
            continue
        key = (od["option_expiry"][i], od["underlying_contract"][i])
        groups[key].append(i)

    # Pre-compute per-group surface summaries
    surface_by_group: dict[tuple, dict] = {}
    for key, idxs in groups.items():
        exp_str, underlying = key

        # Forward price for this underlying
        fwd = forward_by_contract.get(underlying) or front_settle
        if fwd is None:
            continue

        try:
            exp_date = date.fromisoformat(exp_str)
        except ValueError:
            continue
        tte = max((exp_date - as_of_date).days / 365.0, 1.0 / 365.0)

        # Compute IV for each row
        call_rows: list[dict] = []  # {strike, iv, delta}
        put_rows: list[dict] = []

        # LO options are American-style; Black-76 is a European approximation.
        # For OTM/near-ATM strikes the error is typically <1 vol point.
        # Deep ITM puts may show understated IV due to early-exercise premium.
        for i in idxs:
            strike = od["strike"][i]
            settle = od["settlement"][i]
            cp = od["call_put"][i]
            if strike is None or settle is None or strike <= 0:
                continue

            iv = implied_vol(
                market_price=settle,
                forward=fwd,
                strike=strike,
                time_to_expiry=tte,
                rate=_RISK_FREE_RATE,
                call_put=cp,
            )
            if iv is None or iv <= 0:
                continue

            greeks = black76_greeks(fwd, strike, tte, _RISK_FREE_RATE, iv, cp)
            delta_val = abs(greeks.get("delta", float("nan")))
            moneyness = math.log(fwd / strike) if fwd > 0 and strike > 0 else None

            row = {
                "idx": i,
                "strike": strike,
                "iv": iv,
                "delta": delta_val,
                "moneyness": moneyness,
                "greeks": greeks,
            }
            if cp == "C":
                call_rows.append(row)
            else:
                put_rows.append(row)

        n_calls = len(call_rows)
        n_puts = len(put_rows)

        coverage = "PASS" if (n_calls >= 5 and n_puts >= 5) else (
            "WARN" if (n_calls >= 2 and n_puts >= 2) else "FAIL"
        )

        # ATM IV: average call and put IV at the strike with moneyness closest to 0
        atm_iv = None
        all_rows_for_atm = call_rows + put_rows
        if all_rows_for_atm:
            atm_strike = min(
                all_rows_for_atm,
                key=lambda r: abs(r["moneyness"] if r["moneyness"] is not None else float("inf")),
            )["strike"]
            ivs_at_atm = (
                [r["iv"] for r in call_rows if r["strike"] == atm_strike]
                + [r["iv"] for r in put_rows if r["strike"] == atm_strike]
            )
            atm_iv = sum(ivs_at_atm) / len(ivs_at_atm) if ivs_at_atm else None

        # 25-delta IV
        iv_25c, iv_25p = None, None
        if call_rows:
            iv_25c = _interpolate_iv_at_delta(
                [r["strike"] for r in call_rows],
                [r["iv"] for r in call_rows],
                [r["delta"] for r in call_rows],
                0.25,
            )
        if put_rows:
            iv_25p = _interpolate_iv_at_delta(
                [r["strike"] for r in put_rows],
                [r["iv"] for r in put_rows],
                [r["delta"] for r in put_rows],
                0.25,
            )

        rr25 = (iv_25c - iv_25p) if (iv_25c is not None and iv_25p is not None) else None
        bf25 = ((iv_25c + iv_25p) / 2 - atm_iv
                if (iv_25c and iv_25p and atm_iv) else None)

        # Skew slope (IV vs log-moneyness) — combined calls + puts
        all_rows = call_rows + put_rows
        skew = _linear_regression_slope(
            [r["moneyness"] for r in all_rows if r["moneyness"] is not None],
            [r["iv"] for r in all_rows if r["moneyness"] is not None],
        )

        surface_by_group[key] = {
            "fwd": fwd,
            "tte": tte,
            "atm_iv": atm_iv,
            "iv_25d_call": iv_25c,
            "iv_25d_put": iv_25p,
            "risk_reversal_25d": rr25,
            "butterfly_25d": bf25,
            "skew_slope": skew,
            "n_calls": n_calls,
            "n_puts": n_puts,
            "coverage_status": coverage,
            "call_by_idx": {r["idx"]: r for r in call_rows},
            "put_by_idx": {r["idx"]: r for r in put_rows},
        }

    # Build output table
    rows: dict[str, list] = {f.name: [] for f in _SCHEMA}

    for i in range(n):
        if od.get("silver_status", ["PASS"] * n)[i] == "FAIL":
            continue

        exp_str = od["option_expiry"][i]
        underlying = od["underlying_contract"][i]
        key = (exp_str, underlying)
        surf = surface_by_group.get(key)

        if surf is None:
            continue

        strike = od["strike"][i]
        cp = od["call_put"][i]
        settle = od["settlement"][i]
        fwd = surf["fwd"]
        tte = surf["tte"]

        moneyness = math.log(fwd / strike) if (fwd and strike and strike > 0) else None

        # Get pre-computed IV and greeks for this row
        iv_row = surf["call_by_idx"].get(i) if cp == "C" else surf["put_by_idx"].get(i)
        if iv_row:
            b76_iv = iv_row["iv"]
            b76_delta = iv_row["greeks"].get("delta")
            b76_vega = iv_row["greeks"].get("vega")
        else:
            b76_iv = b76_delta = b76_vega = None

        rows["trade_date"].append(as_of_date.isoformat())
        rows["option_expiry"].append(exp_str)
        rows["option_symbol"].append(od["option_symbol"][i])
        rows["underlying_contract"].append(underlying)
        rows["strike"].append(strike)
        rows["call_put"].append(cp)
        rows["settlement"].append(settle)
        rows["forward_price"].append(fwd)
        rows["time_to_expiry_years"].append(tte)
        rows["moneyness_log"].append(moneyness)
        rows["black76_iv"].append(b76_iv)
        rows["black76_delta"].append(b76_delta)
        rows["black76_vega"].append(b76_vega)
        rows["market_delta"].append(od["delta"][i])
        rows["atm_iv"].append(surf["atm_iv"])
        rows["iv_25d_call"].append(surf["iv_25d_call"])
        rows["iv_25d_put"].append(surf["iv_25d_put"])
        rows["risk_reversal_25d"].append(surf["risk_reversal_25d"])
        rows["butterfly_25d"].append(surf["butterfly_25d"])
        rows["skew_slope"].append(surf["skew_slope"])
        rows["valid_call_strikes"].append(surf["n_calls"])
        rows["valid_put_strikes"].append(surf["n_puts"])
        rows["coverage_status"].append(surf["coverage_status"])
        rows["source_id"].append(od["source_id"][i])
        rows["price_note"].append(od.get("price_note", [""] * n)[i])

    return pa.table(rows, schema=_SCHEMA)

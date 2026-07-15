"""
Futures-options agreement classification.

Determines whether the futures curve and options surface are sending
consistent signals about directional risk.

States (from the spec):
  confirmed_upside_risk       futures + options both signal upside
  confirmed_downside_risk     futures + options both signal downside/demand risk
  non_directional_uncertainty high vol but mixed direction
  futures_only_repricing      futures moved but options IV unchanged
  options_only_repricing      IV moved but futures flat
  cross_market_disagreement   futures and options point opposite directions
  no_material_change          neither moved significantly
  insufficient_data           missing or failed inputs
"""
from __future__ import annotations

from typing import Optional

# Thresholds (E2: normalized to price-relative units so they carry across
# products — ±0.10 $/month on a $70 WTI handle ≈ ±0.14%/month; meaningless
# on a $3.50 gas handle. RR/IV thresholds are already unitless vol points.)
_SLOPE_BACKWARDATION_PCT = -0.0014   # slope/front_price per month → upside risk
_SLOPE_CONTANGO_PCT = 0.0014         # → downside / supply glut
_RR_UPSIDE = 0.02              # risk reversal > 2% → call skew (upside bid)
_RR_DOWNSIDE = -0.02           # risk reversal < -2% → put skew (downside bid)


def classify(
    front_back_slope: Optional[float],    # from futures features ($/month)
    contango_flag: Optional[bool],
    risk_reversal_25d: Optional[float],   # from option surface
    atm_iv: Optional[float],
    prior_atm_iv: Optional[float],        # prior day (may be None)
    prior_slope: Optional[float],
    eia_supply_signal: Optional[str] = None,    # "draw" / "build" / "neutral" / None
    eia_scenario_trigger: Optional[str] = None, # "bull_confirmed" / "bear_watch" / "bear_confirmed" / "none"
    front_settlement: Optional[float] = None,   # E2: normalizes slope to %/month
) -> dict:
    """
    Return a dict with:
      state             (one of the 8 states above)
      confidence        "high" / "medium" / "low"
      evidence          list of supporting signals

    E2: the slope signal is judged in price-relative units (slope ÷ front
    price, per month) so thresholds carry across products. front_settlement
    is therefore a required input like slope and ATM IV — a missing settle
    means the classification cannot be normalized honestly, so it degrades
    to insufficient_data the same way the other missing inputs do (no silent
    reference-price assumption).
    """
    evidence: list[str] = []

    if front_back_slope is None or atm_iv is None or not front_settlement:
        return {
            "state": "insufficient_data",
            "confidence": "low",
            "evidence": ["missing futures slope, ATM IV, or front settlement"],
        }

    # ── Futures signal (price-relative slope) ──
    ref_price = front_settlement
    slope_pct = front_back_slope / ref_price
    if slope_pct < _SLOPE_BACKWARDATION_PCT:
        futures_signal = "upside"
        evidence.append(
            f"backwardation: slope={front_back_slope:.2f}$/month ({slope_pct:.2%}/mo)")
    elif slope_pct > _SLOPE_CONTANGO_PCT:
        futures_signal = "downside"
        evidence.append(
            f"contango: slope={front_back_slope:.2f}$/month ({slope_pct:.2%}/mo)")
    else:
        futures_signal = "neutral"
        evidence.append(f"flat curve: slope={front_back_slope:.2f}$/month")

    # ── Slope change (also price-relative: 0.07%/mo ≈ $0.05 on WTI) ──
    if prior_slope is not None:
        slope_change = front_back_slope - prior_slope
        if abs(slope_change / ref_price) > 0.0007:
            evidence.append(f"slope moved {slope_change:+.2f}$/month vs prior day")

    # ── Options signal (risk reversal) ──
    if risk_reversal_25d is not None:
        if risk_reversal_25d > _RR_UPSIDE:
            options_signal = "upside"
            evidence.append(f"call skew: 25d RR={risk_reversal_25d:.1%}")
        elif risk_reversal_25d < _RR_DOWNSIDE:
            options_signal = "downside"
            evidence.append(f"put skew: 25d RR={risk_reversal_25d:.1%}")
        else:
            options_signal = "neutral"
            evidence.append(f"balanced skew: 25d RR={risk_reversal_25d:.1%}")
    else:
        options_signal = "unknown"

    # ── IV change ──
    iv_moved = False
    if prior_atm_iv is not None and prior_atm_iv > 0:
        iv_change_pct = (atm_iv - prior_atm_iv) / prior_atm_iv
        if abs(iv_change_pct) > 0.05:
            iv_moved = True
            evidence.append(f"ATM IV moved {iv_change_pct:+.1%} vs prior day")

    # ── EIA supply signal ──
    if eia_supply_signal == "draw":
        evidence.append(f"EIA: crude stock draw (supply tightening)")
        if eia_scenario_trigger == "bull_confirmed":
            evidence.append("EIA draw exceeds bull scenario threshold (>3M bbl)")
    elif eia_supply_signal == "build":
        evidence.append(f"EIA: crude stock build (supply loosening)")
        if eia_scenario_trigger in ("bear_watch", "bear_confirmed"):
            evidence.append(f"EIA build triggers {eia_scenario_trigger}")

    # ── Classify ──
    futures_moved = abs(front_back_slope) > 0.05 or (prior_slope is not None and abs(front_back_slope - prior_slope) > 0.05)

    if options_signal == "unknown":
        if futures_signal == "upside":
            state = "futures_only_repricing" if futures_moved else "no_material_change"
        elif futures_signal == "downside":
            state = "futures_only_repricing" if futures_moved else "no_material_change"
        else:
            state = "no_material_change"
        confidence = "low"

    elif futures_signal == options_signal == "upside":
        state = "confirmed_upside_risk"
        confidence = "high" if eia_supply_signal == "draw" else "high"
        if eia_supply_signal == "draw":
            evidence.append("all three signals aligned: futures backwardation + call skew + EIA draw")

    elif futures_signal == options_signal == "downside":
        state = "confirmed_downside_risk"
        confidence = "high"
        if eia_supply_signal == "build":
            evidence.append("all three signals aligned: futures contango + put skew + EIA build")

    elif futures_signal == "upside" and options_signal == "downside":
        state = "cross_market_disagreement"
        confidence = "medium"
        evidence.append("futures backwardated but options showing put skew")

    elif futures_signal == "downside" and options_signal == "upside":
        state = "cross_market_disagreement"
        confidence = "medium"
        evidence.append("futures in contango but options showing call skew")

    elif futures_signal == "neutral" and options_signal == "neutral":
        if iv_moved:
            state = "non_directional_uncertainty"
            confidence = "medium"
        else:
            state = "no_material_change"
            confidence = "high"

    elif futures_moved and not iv_moved:
        state = "futures_only_repricing"
        confidence = "medium"

    elif iv_moved and not futures_moved:
        state = "options_only_repricing"
        confidence = "medium"

    else:
        state = "non_directional_uncertainty"
        confidence = "low"

    return {
        "state": state,
        "confidence": confidence,
        "evidence": evidence,
        "inputs": {
            "front_back_slope": front_back_slope,
            "atm_iv": atm_iv,
            "risk_reversal_25d": risk_reversal_25d,
        },
    }

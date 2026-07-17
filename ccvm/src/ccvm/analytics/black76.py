"""
Black-76 model for European futures options.

Used to compute:
  - Implied volatility from market prices (numerical inversion via bisection)
  - Option price given forward, strike, time, rate, vol (for validation)
  - Delta, gamma, vega, theta under Black-76

Note:
  Black-76 is kept as a European reference pricer and for comparison.
  The primary IV/greeks for American-style product options use the
  Barone-Adesi & Whaley (BAW) model in baw.py.

References:
  Black, F. (1976). "The pricing of commodity contracts."
  Journal of Financial Economics 3, 167-179.
"""
from __future__ import annotations

import math
from typing import Optional

_SQRT_2PI = math.sqrt(2 * math.pi)
_INV_SQRT_2 = 1.0 / math.sqrt(2)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x * _INV_SQRT_2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def black76_price(
    forward: float,
    strike: float,
    time_to_expiry: float,  # in years
    rate: float,            # risk-free rate (continuously compounded)
    vol: float,
    call_put: str,          # "C" or "P"
) -> float:
    """Black-76 European futures option price."""
    if time_to_expiry <= 0:
        # At expiry: intrinsic value
        if call_put == "C":
            return max(forward - strike, 0.0)
        else:
            return max(strike - forward, 0.0)

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * time_to_expiry) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    df = math.exp(-rate * time_to_expiry)

    if call_put == "C":
        return df * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    else:
        return df * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))


def black76_greeks(
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    vol: float,
    call_put: str,
    multiplier: float = 1.0,
) -> dict:
    """Compute Black-76 delta, gamma, vega, theta."""
    if time_to_expiry <= 0 or vol <= 0:
        return {"delta": float("nan"), "gamma": float("nan"),
                "vega": float("nan"), "theta": float("nan")}

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * time_to_expiry) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    df = math.exp(-rate * time_to_expiry)
    pdf_d1 = _norm_pdf(d1)

    if call_put == "C":
        delta = df * _norm_cdf(d1)
        price = df * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    else:
        delta = -df * _norm_cdf(-d1)
        price = df * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))

    gamma = df * pdf_d1 / (forward * vol * sqrt_t)
    vega = df * forward * pdf_d1 * sqrt_t / 100.0  # per 1% vol move
    theta = (
        -(df * forward * pdf_d1 * vol / (2 * sqrt_t))
        + rate * price * df  # note: sign convention varies
    ) / 365.0  # per calendar day

    return {
        "delta": delta * multiplier,
        "gamma": gamma * multiplier,
        "vega": vega * multiplier,
        "theta": theta * multiplier,
    }


def implied_vol(
    market_price: float,
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    call_put: str,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> Optional[float]:
    """
    Compute implied volatility via bisection.
    Returns None if:
      - time_to_expiry <= 0
      - market_price <= intrinsic value (below-intrinsic)
      - no convergence within max_iter iterations
    """
    if time_to_expiry <= 0:
        return None

    # Check intrinsic value
    df = math.exp(-rate * time_to_expiry)
    if call_put == "C":
        intrinsic = max(df * (forward - strike), 0.0)
    else:
        intrinsic = max(df * (strike - forward), 0.0)

    if market_price <= intrinsic - tol:
        return None  # below intrinsic — model price can never reach this

    # Bisection between vol_lo and vol_hi
    vol_lo, vol_hi = 1e-4, 5.0  # 0.01% to 500% vol

    price_lo = black76_price(forward, strike, time_to_expiry, rate, vol_lo, call_put)
    price_hi = black76_price(forward, strike, time_to_expiry, rate, vol_hi, call_put)

    if market_price < price_lo:
        return vol_lo
    if market_price > price_hi:
        return None  # price above even 500% vol — unrealistic

    for _ in range(max_iter):
        vol_mid = (vol_lo + vol_hi) / 2.0
        price_mid = black76_price(forward, strike, time_to_expiry, rate, vol_mid, call_put)
        if abs(price_mid - market_price) < tol:
            return vol_mid
        if price_mid < market_price:
            vol_lo = vol_mid
        else:
            vol_hi = vol_mid

    return (vol_lo + vol_hi) / 2.0  # best estimate if not converged

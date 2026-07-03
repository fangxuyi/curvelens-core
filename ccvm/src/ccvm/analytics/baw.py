"""
Barone-Adesi & Whaley (1987) quadratic approximation for American futures options.

Specialized to b = 0 (cost-of-carry = 0 for options on futures).

WTI (LO) options on NYMEX CL futures are American-style. Black-76 understates
their value because it ignores early-exercise optionality:
  - Puts: early exercise is optimal when deeply ITM (receive K now > wait)
  - Calls: can also be optimal for deep ITM calls when r > 0 (interest on K
    exceeds remaining time value)

For near-ATM strikes and typical WTI vols (20-40%, T < 6mo), the early-exercise
premium is <1 vol point. It is material for deep ITM options.

References:
  Barone-Adesi, G. & Whaley, R.E. (1987). "Efficient Analytic Approximation
  of American Option Values." Journal of Finance 42(2), 301-320.
"""
from __future__ import annotations

import math
from typing import Optional

from .black76 import black76_price

_SQRT_2PI = math.sqrt(2 * math.pi)
_INV_SQRT_2 = 1.0 / math.sqrt(2)


def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x * _INV_SQRT_2))


def _d1(F: float, K: float, T: float, vol: float) -> float:
    return (math.log(F / K) + 0.5 * vol * vol * T) / (vol * math.sqrt(T))


def _q2(r: float, vol: float, h: float) -> float:
    M = 2.0 * r / (vol * vol)
    return (1.0 + math.sqrt(1.0 + 4.0 * M / h)) / 2.0


def _q1(r: float, vol: float, h: float) -> float:
    M = 2.0 * r / (vol * vol)
    return (1.0 - math.sqrt(1.0 + 4.0 * M / h)) / 2.0  # negative


def _critical_call(K: float, T: float, r: float, vol: float) -> float:
    """
    Find F* > K where an American call on a futures contract (b=0) becomes
    optimal to exercise early.

    Solves: c_euro(F*) = F* - K - (F*/q2) * (1 - exp(-rT)*N(d1(F*)))
    i.e.  g(F*) = c_euro(F*) - F* + K + (F*/q2)*(1 - exp(-rT)*N(d1(F*))) = 0
    """
    df = math.exp(-r * T)
    h = 1.0 - df
    q = _q2(r, vol, h)

    def g(x: float) -> float:
        d = _d1(x, K, T, vol)
        c = black76_price(x, K, T, r, vol, "C")
        return c - x + K + (x / q) * (1.0 - df * _N(d))

    # g(K) > 0, g(large) < 0 — bisect on [K, hi]
    lo, hi = K, K * 20.0
    g_lo = g(lo)
    g_hi = g(hi)

    # Expand hi if not bracketed
    for _ in range(10):
        if g_lo * g_hi < 0:
            break
        hi *= 5.0
        g_hi = g(hi)
    else:
        return float("inf")  # effectively never exercise early

    for _ in range(60):
        mid = (lo + hi) / 2.0
        g_mid = g(mid)
        if abs(g_mid) < 1e-7:
            return mid
        if g_lo * g_mid < 0:
            hi, g_hi = mid, g_mid
        else:
            lo, g_lo = mid, g_mid

    return (lo + hi) / 2.0


def _critical_put(K: float, T: float, r: float, vol: float) -> float:
    """
    Find F** < K where an American put on a futures contract (b=0) becomes
    optimal to exercise early.

    Solves: p_euro(F**) = K - F** - (F**/q1)*(1 - exp(-rT)*N(-d1(F**)))
    i.e.  g(F**) = p_euro(F**) - K + F** - (F**/q1)*(1 - exp(-rT)*N(-d1(F**))) = 0
    Note: q1 < 0, so -(F**/q1) > 0.
    """
    df = math.exp(-r * T)
    h = 1.0 - df
    q = _q1(r, vol, h)  # negative

    def g(x: float) -> float:
        if x <= 0:
            return float("inf")
        d = _d1(x, K, T, vol)
        p = black76_price(x, K, T, r, vol, "P")
        return p - K + x - (x / q) * (1.0 - df * _N(-d))

    # g(0+) < 0, g(K) > 0 — bisect on (0, K)
    lo, hi = 1e-4, K
    g_lo = g(lo)
    g_hi = g(hi)

    if g_lo * g_hi >= 0:
        return 0.0  # exercise immediately everywhere

    for _ in range(60):
        mid = (lo + hi) / 2.0
        g_mid = g(mid)
        if abs(g_mid) < 1e-7:
            return mid
        if g_lo * g_mid < 0:
            hi, g_hi = mid, g_mid
        else:
            lo, g_lo = mid, g_mid

    return (lo + hi) / 2.0


def baw_price(
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    vol: float,
    call_put: str,
) -> float:
    """
    BAW American option price on a futures contract (cost-of-carry b = 0).

    Returns intrinsic value when time_to_expiry <= 0.
    Falls back to Black-76 when rate = 0 (no early-exercise incentive).
    """
    F, K, T, r, σ = forward, strike, time_to_expiry, rate, vol

    if T <= 0:
        return max(F - K, 0.0) if call_put == "C" else max(K - F, 0.0)

    if σ <= 0:
        df = math.exp(-r * T)
        return max(df * (F - K), 0.0) if call_put == "C" else max(df * (K - F), 0.0)

    if r <= 0:
        # No interest rate → no early-exercise benefit → Black-76 is exact
        return black76_price(F, K, T, r, σ, call_put)

    df = math.exp(-r * T)
    h = 1.0 - df

    if call_put == "C":
        # Immediate exercise value
        if F - K <= 0:
            # OTM call: early exercise never optimal
            return black76_price(F, K, T, r, σ, "C")

        q = _q2(r, σ, h)
        F_star = _critical_call(K, T, r, σ)

        if F >= F_star:
            return F - K

        d1_star = _d1(F_star, K, T, σ)
        A2 = (F_star / q) * (1.0 - df * _N(d1_star))
        euro = black76_price(F, K, T, r, σ, "C")
        return euro + A2 * (F / F_star) ** q

    else:  # PUT
        if K - F <= 0:
            # OTM put: early exercise never optimal
            return black76_price(F, K, T, r, σ, "P")

        q = _q1(r, σ, h)  # negative
        F_star2 = _critical_put(K, T, r, σ)

        if F <= F_star2:
            return K - F

        d1_star = _d1(F_star2, K, T, σ)
        A1 = -(F_star2 / q) * (1.0 - df * _N(-d1_star))
        euro = black76_price(F, K, T, r, σ, "P")
        return euro + A1 * (F / F_star2) ** q


def baw_implied_vol(
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
    Implied vol from BAW American option price via bisection.

    American intrinsic value (no discounting) is used as the lower bound check
    since early exercise is always available.
    Returns None if the price is below intrinsic or no convergence.
    """
    if time_to_expiry <= 0:
        return None

    # American intrinsic (undiscounted — can always exercise now)
    intrinsic = max(forward - strike, 0.0) if call_put == "C" else max(strike - forward, 0.0)
    if market_price < intrinsic - tol:
        return None

    vol_lo, vol_hi = 1e-4, 5.0

    price_lo = baw_price(forward, strike, time_to_expiry, rate, vol_lo, call_put)
    price_hi = baw_price(forward, strike, time_to_expiry, rate, vol_hi, call_put)

    if market_price <= price_lo:
        return vol_lo
    if market_price >= price_hi:
        return None  # price implies > 500% vol — treat as bad data

    for _ in range(max_iter):
        vol_mid = (vol_lo + vol_hi) / 2.0
        price_mid = baw_price(forward, strike, time_to_expiry, rate, vol_mid, call_put)
        if abs(price_mid - market_price) < tol:
            return vol_mid
        if price_mid < market_price:
            vol_lo = vol_mid
        else:
            vol_hi = vol_mid

    return (vol_lo + vol_hi) / 2.0


def baw_greeks(
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    vol: float,
    call_put: str,
    multiplier: float = 1.0,
) -> dict:
    """BAW delta and vega via central finite differences."""
    if time_to_expiry <= 0 or vol <= 0:
        return {"delta": float("nan"), "vega": float("nan")}

    dF = forward * 0.001   # 0.1% forward bump
    dv = 0.001             # 0.1 vol point bump

    p_up_F  = baw_price(forward + dF, strike, time_to_expiry, rate, vol, call_put)
    p_dn_F  = baw_price(forward - dF, strike, time_to_expiry, rate, vol, call_put)
    p_up_v  = baw_price(forward, strike, time_to_expiry, rate, vol + dv, call_put)
    p_dn_v  = baw_price(forward, strike, time_to_expiry, rate, vol - dv, call_put)

    delta = (p_up_F - p_dn_F) / (2.0 * dF)
    vega  = (p_up_v - p_dn_v) / (2.0 * dv) / 100.0  # per 1% vol move

    return {
        "delta": delta * multiplier,
        "vega":  vega  * multiplier,
    }

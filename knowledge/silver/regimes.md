# Silver Regimes and Historical Reference Hierarchy

*Last reviewed: 2026-07-23 — provenance: official references plus uncalibrated interpretation priors*

Silver begins without enough local SI and SO history for stable regime bands.
Analysts must use exact values and this hierarchy:

1. Use `history_context` for measures produced by this deployment and state its
   observation count with every percentile.
2. Use the current curve, SO ATM volatility, 25-delta risk reversal, and
   25-delta butterfly as separate dimensions. Do not import WTI or Gold bands.
3. Use the macro and industrial observations only at their documented cadence.
   No current Silver ETF-volatility index is configured: FRED's `VXSLVCLS`
   ended in 2022 and must not be presented as a live benchmark.

Silver is a hybrid monetary and industrial commodity. Falling real yields or a
weaker dollar can support investment demand, while electronics, photovoltaic,
and broader manufacturing conditions can create a different industrial
signal. A conflict between those channels is analytically meaningful.

Curve contango can include financing, storage, insurance, lease conditions,
delivery optionality, and liquidity. Backwardation can indicate tighter
near-term availability, but neither shape alone proves a physical shortage.

Every report should give the exact settlement, curve spreads, ATM IV, risk
reversal, butterfly, positioning, and dated macro/industrial observations.
Put the young-history note once in data limits; do not let it replace analysis.

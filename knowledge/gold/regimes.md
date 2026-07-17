# Gold Regimes

*Last reviewed: 2026-07-16 — provenance: authored priors, not yet calibrated*

The initial Gold deployment intentionally does not hard-code numerical regime
bands. Curve, ATM volatility, risk reversal, butterfly, realized volatility,
and variance-risk-premium context should come from this deployment's measured
history. Treat percentiles as young-history statistics until roughly one year
of settlements has accumulated.

Interpretation priors to test, not facts to force onto the data:

- Gold can respond materially to real-rate, USD, central-bank, and geopolitical
  catalysts; direction must be confirmed by collected market data.
- Skew conventions should be learned from OG history rather than inherited from
  WTI. A WTI put-skew norm is not portable to Gold.
- Delivery-month curve behavior may be affected by financing and physical-market
  mechanics; avoid petroleum-style inventory interpretations.

# Gold Regimes and Historical Reference Hierarchy

*Last reviewed: 2026-07-21 — provenance: measured CurveLens history plus the
official CME and FRED/CBOE references linked below*

Gold does not yet have enough locally accumulated OG settlement history for
stable futures or option percentiles. That is a limitation of the local sample,
not an absence of all historical context. Analysts must use this hierarchy and
must not describe the report simply as having "severe history limits":

1. Use `history_context` for the exact GC and OG measures produced by this
   deployment. State its observation count whenever quoting a percentile.
2. Use the profile-collected CBOE Gold ETF Volatility Index (`GVZCLS`) for a
   longer volatility-regime comparison. Report its rolling observation count,
   median, range, and percentile from the macro section. GVZ is based on GLD
   ETF options, so it is a proxy; never compare its level one-for-one with OG
   ATM implied volatility or present it as a COMEX option measure.
3. Use the structural and episode references below only as authored comparison
   anchors. They are not fitted trading thresholds.

## Volatility and skew anchors

- CME's CVOL framework separates expected volatility into ATM volatility,
  skew, and convexity. CurveLens' ATM IV, 25-delta risk reversal, and
  25-delta butterfly are analogous descriptive dimensions, but they are not
  the CME CVOL index. Source: [CME Group Volatility Indexes](https://www.cmegroup.com/market-data/cme-group-benchmark-administration/cme-group-volatility-indexes.html).
- A negative 25-delta risk reversal means comparable downside puts carry more
  implied volatility than upside calls. A positive 25-delta butterfly means
  the wings are richer than the center of the smile. Always report the actual
  values in volatility points before interpreting them.
- As an episode anchor rather than a normal range, CME reported that on
  2023-10-02 five-percent-out-of-the-money gold puts were near 20% implied
  volatility while comparable calls were near 15.5%, the most negative skew in
  more than a year. Source: [CME, Gold Volatility Rises as Expectations Change](https://www.cmegroup.com/openmarkets/metals/2023/Gold-Volatility-Rises-as-Expectations-Change.html).

## Curve anchors

- Gold contango can reflect financing, storage, insurance, lease conditions,
  delivery optionality, and liquidity. Backwardation can reflect a convenience
  yield or near-term scarcity. Source: [CME, Contango and Backwardation](https://www.cmegroup.com/education/courses/introduction-to-precious-metals/what-is-contango-and-backwardation).
- Do not import WTI's backwardation frequency, dollar-spread bands, or inventory
  interpretation. Until GC history matures, report the exact M1-M2, M1-M3,
  M1-M6, and annualized roll-yield values and compare them with prior local
  dates without assigning an unsupported historical regime label.

## Reporting rule

Every Gold report should give today's exact settlement, curve spreads, ATM IV,
risk reversal, butterfly, and relevant macro levels. Compare them with measured
history or the GVZ proxy where valid. Put the young-history note once in the
data-limits section; do not let it replace the market analysis.

Interpretation priors to test, not facts to force onto the data:

- Gold can respond materially to real-rate, USD, central-bank, and geopolitical
  catalysts; direction must be confirmed by collected market data.
- Skew conventions should be learned from OG history rather than inherited from
  WTI. A WTI put-skew norm is not portable to Gold.
- Delivery-month curve behavior may be affected by financing and physical-market
  mechanics; avoid petroleum-style inventory interpretations.

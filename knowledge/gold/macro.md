# Gold Macro Analysis

*Last reviewed: 2026-07-20 — provenance: verified source definitions plus authored interpretation priors; relationships are not yet internally calibrated*

Gold has no petroleum-style weekly physical balance sheet. Its daily analysis
instead needs a distinct macro layer. The layer is descriptive context: it does
not make macro inputs part of the agreement score, claim causality, or turn a
settled curve into an executable trade.

## Phase 1 — automated public daily series

The product profile declares the series, transformations, and directional
priors. `collect_day.py --source macro` obtains trailing history from the
official [FRED observations API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html),
using `FRED_API_KEY`; raw responses remain immutable and normalized observations
are stored in `silver/macro`. Missing credentials cause an explicit skip.

| Information | Series / official source | Collection and transformation | Initial interpretation prior |
|---|---|---|---|
| Real opportunity cost | [DFII10, 10Y TIPS real yield](https://fred.stlouisfed.org/series/DFII10) | Daily; latest level and change in bp | Falling real yield is usually supportive for flat Gold; rising is usually a headwind. This can fail when safe-haven or official-sector demand dominates. |
| Dollar translation | [DTWEXBGS, broad nominal USD](https://fred.stlouisfed.org/series/DTWEXBGS) | Daily; index return in percent | A weaker broad dollar is usually supportive for USD Gold, and vice versa. |
| Inflation compensation | [T10YIE, 10Y breakeven](https://fred.stlouisfed.org/series/T10YIE) | Daily; change in bp | Rising expected inflation is a mild supportive prior only when it is not offset by a larger rise in real yields. |
| Financing/carry anchor | [DGS3MO, 3M Treasury](https://fred.stlouisfed.org/series/DGS3MO) | Daily; annual percent | Compare with curve-implied carry. The residual can contain financing, storage, lease rates, convenience yield, and liquidity. It is not an arbitrage signal. |
| Rate decomposition | [DGS10, 10Y Treasury](https://fred.stlouisfed.org/series/DGS10) | Daily; change in bp | Read jointly with real yield and breakeven; nominal yields alone are ambiguous for Gold. |

The real-yield and dollar selection is consistent with the World Gold Council's
[two-factor comparison](https://www.gold.org/goldhub/research/qaurum-vs-us-real-rates-and-dollar-model).
WGC also documents that the historical inverse real-yield relationship has at
times been overwhelmed by central-bank buying and risk demand
([discussion](https://www.gold.org/goldhub/gold-focus/2025/06/you-asked-we-answered-are-fiscal-concerns-driving-gold)).

## Daily analysis

### Flat price

Report every level with its observation date because FRED releases can lag the
trade date. Convert yield changes to basis points and the dollar-index change to
percent. A transparent sign score summarizes whether the latest changes are
supportive, a headwind, or mixed; it is a prior, not a fitted forecast. Compare
it with the GC return, futures/options agreement, COT positioning, and dated
catalysts. A macro/market disagreement is itself the observation—do not force a
directional conclusion.

Once enough CurveLens dates have accumulated, add rolling 20/60/252-session
correlations and robust betas of GC returns to real-yield and dollar changes.
Mark shorter samples as young history and preserve regime breaks rather than
publishing a single timeless coefficient.

### Curve shape

Gold term structure is principally a carry/financing/lease/storage problem, not
an inventory draw/build signal. Compute annualized front roll yield from GC,
invert it to an implied carry convention, and compare it with the 3-month
Treasury yield. Investigate a persistent gap with physical lease rates,
financing, storage, delivery optionality, and contract liquidity. WGC's
[gold deposit-rate note](https://www.gold.org/goldhub/research/gold-deposit-rates-guidance-paper)
is background on lease/deposit-rate mechanics. Settlement analytics do not
establish an executable cash-and-carry opportunity.

### Volatility surface

Read macro changes against three OG surface dimensions:

- ATM IV: macro-event uncertainty or an unusually large rates/USD impulse may
  lift the level, but direction and magnitude must be measured from history.
- 25-delta risk reversal: supportive macro plus call skew is coherent upside
  demand; headwind macro plus put skew is coherent downside demand; disagreement
  is a divergence flag, not an error.
- 25-delta butterfly and term structure: event concentration can raise wings or
  front expiries around FOMC, CPI, payrolls, or PCE dates.

Do not label an economic “surprise” without a licensed, timestamped consensus
source. In its absence, use the observed post-release changes in GC, yields, the
dollar, ATM IV, RR, and BF. Official schedule sources are the
[Federal Reserve FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm),
[BLS release calendar](https://www.bls.gov/schedule/), and
[BEA release schedule](https://www.bea.gov/news/schedule/full).

## Phase 2 — important slower or licensed inputs

- Official-sector demand: quarterly World Gold Council central-bank tables and
  IMF reserve data. Collect publication vintage as well as period; never
  forward-fill a quarterly flow as if it were daily. WGC publishes downloadable
  tables with its [Gold Demand Trends central-bank analysis](https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-q1-2026/central-banks).
- Gold ETF holdings/flows: use issuer or licensed aggregate feeds with explicit
  units, fund coverage, and publication time. Do not silently scrape a changing
  webpage or mix tonnes and ounces.
- Physical/lease indicators: add only when source rights, units, fixing time,
  and continuity are established.
- Economic-release surprises: require timestamped actual, consensus, prior, and
  revision fields. Event studies must prevent look-ahead bias.

These inputs are intentionally documented but not automated in Phase 1 because
their cadence, revisions, coverage, or licensing make a superficial daily feed
more misleading than an explicit gap.


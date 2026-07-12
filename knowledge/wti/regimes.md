# WTI Regime Norms — What "Normal" Looks Like

*Reference values for reading the daily numbers. These are long-run norms from
market history up to the knowledge cutoff; the mechanical percentiles in the
brief's Context block (gold/history_context) are the live measurement — use
both: percentiles say "unusual vs recent history," this file says "unusual vs
the market's long-run character."*

## Volatility

| Front-month ATM IV | Reading |
|---|---|
| < 25% | low-vol regime (calm, range-bound; typical mid-cycle) |
| 25–40% | normal |
| 40–60% | stressed (supply shocks, macro risk-off) |
| > 60% | crisis (2008, 2020, early 2022) |

- Term structure of vol is usually **downward-sloping** (front > deferred).
  Inverted (front *below* back) is unusual and typically post-event decay.
- Vol clusters around EIA Wednesdays and OPEC meetings; a front-vs-2nd-expiry
  IV spread widening into an event is event premium, not a regime change.

## Skew

- **WTI is normally put-skewed** (25Δ RR slightly negative, roughly −5 to 0):
  producers hedge downside and demand shocks dominate memory.
- **Sustained positive RR (call skew) is a supply-fear regime** — it appears
  around wars, sanctions, embargo threats, and OPEC squeeze scenarios. If the
  brief shows RR25 > +2% for days, that is itself a signal worth a sentence.
- Butterfly (smile convexity) rises with tail fear in either direction.

## Curve

- **Backwardation** is the historical norm (~60% of the time) and associates
  with tight inventories; steep backwardation (> $0.50/mo) = physical
  tightness at Cushing.
- **Contango** signals surplus; "super-contango" (> $1/mo) = storage stress
  (2009, 2020 — see analogs.md).
- Cushing stocks vs the prompt spread is *the* WTI relationship: Cushing near
  tank-bottoms (~20 MMbbl) → violent backwardation risk; Cushing near working
  capacity (probably ~75–80 MMbbl) → contango and negative-price tail risk.

## Fundamentals reference points

- US commercial crude stocks: roughly 400–460 MMbbl in recent years; SPR
  ~300–400 MMbbl post-2022 drawdowns.
- Refinery utilization: ~85–95% normal band; > 95% is running hot (summer);
  < 80% implies turnarounds or disruption.
- A weekly draw/build of ±3 MMbbl is notable; ±6 MMbbl is large. Judge vs the
  seasonal norm (see seasonality.md), not the raw number alone.

## Cross-market context

- **Brent–WTI**: Brent normally trades $2–6 over WTI (freight/quality/export
  arb). A collapsing spread = weak Atlantic-basin demand or strong US exports;
  a blowout > $8 = trapped US barrels (pre-2015 export-ban dynamics).
- USD strength pressures crude (inverse correlation, regime-dependent).

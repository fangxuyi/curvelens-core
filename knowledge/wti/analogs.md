# WTI Historical Analogs — Dated Episodes for Comparison

*For "biggest since X" and "this looks like Y" narrative. Dates and magnitudes
are as-remembered reference points up to the model knowledge cutoff — verify
specifics before quoting precise figures. The agent may append new episodes
(e.g., from this system's own history) via PR.*

## Supply shocks (call-skew / backwardation regimes)

- **2019-09-14 — Abqaiq–Khurais attack**: ~5.7 MMb/d of Saudi processing hit;
  largest single-day Brent jump on record (~+15%); vol and call skew spiked,
  then faded within weeks as capacity returned. Template for: attack headlines
  → fast spike, fast decay unless capacity is actually lost.
- **2022-02/03 — Russia invades Ukraine**: WTI from ~$90 to ~$130 intraday;
  ATM IV > 80%, extreme backwardation, RR strongly positive. Template for:
  sanction-risk regimes where call skew persists for months.
- **2023-10 — Israel–Hamas**: geopolitical premium episodes with no physical
  supply loss → spikes that decay in days; skew moves more than spot.
- **2026-06 — Strait of Hormuz scare** (in this system's own gold history):
  IV spike then normalization as flows resumed; the extracted catalysts and
  restated IV history around 2026-06-24 → 07-02 show the decay pattern.

## Demand shocks / surplus (put-skew / contango regimes)

- **2008-H2 — GFC**: $147 → $32 in six months; super-contango, storage plays.
- **2014-11 — OPEC lets prices fall** (Thanksgiving meeting): no-cut decision
  → $70s to $40s over months. Template for: OPEC *inaction* as a bear shock.
- **2020-04-20 — negative WTI**: CLK20 settled at **−$37.63** the day before
  its LTD (Apr 21); Cushing effectively full, longs trapped in delivery.
  LOK20 options had expired Apr 16 — a key reminder that **option expiry
  precedes the futures chaos window** (see conventions.md expiry rules).
  Template for: Cushing-full + expiring-front dynamics.

## Policy / OPEC templates

- **2016-11-30 — OPEC+ cut agreement** (first with Russia): sustained rally
  regime change; watch for "production discipline" language.
- **2021–2022 — SPR releases** (~180 MMbbl announced 2022-03): bearish
  headline impact but also removed downside insurance later; SPR *refill*
  chatter acts as a soft floor bid.
- **2023-04 & 2023-06 — surprise OPEC+ voluntary cuts**: weekend announcements
  → Monday gaps; template for why the brief flags OPEC meeting dates in
  advance (calendar.yaml `dated`).

## Usage guidance

When today's Context block shows an extreme percentile, check whether one of
these templates fits *mechanistically* (what's tight: supply, demand, storage,
or policy?) before reaching for the analogy — the point is the mechanism, not
the price path.

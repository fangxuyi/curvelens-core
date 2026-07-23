# AGENTS.md — CurveLens Copper Deployment

This is the operational runbook for the experimental COMEX Copper deployment.
Follow it with the repository-level `AGENTS.md`.

## Status and scope

Status: **experimental — validation only**.

Analyze settled COMEX Copper futures (`HG`) and monthly Copper options (`HX`).
Exclude weekly Copper options, intraday data, and live delivery. Copper is a
shared-framework implementation, not a Silver or Gold fork.

Product facts belong in `ccvm/config/markets/copper.yaml`, calendar rules in
`ccvm/src/ccvm/reference/copper_calendar.py`, interpretation in
`knowledge/copper/`, and operating policy here.

## Deployment environment

Before every runtime command:

```bash
export CCVM_PRODUCT=copper
```

Runtime state resolves to `ccvm/data/products/copper/`. Never use another
product's runtime directory.

## Product sources

- Futures: profile-configured HG individual contracts. Yahoo `.CMX` symbols
  are a bootstrap assumption pending comparison with official CME settlements.
- Options: CME Section 64 Metals Option Products PDF, selecting monthly
  `HX CALL` and `HX PUT` only.
- Positioning: CFTC Copper code `085692`.
- Macro/industrial proxies: profile-configured FRED series; set `FRED_API_KEY`
  and retain observation dates and cadence.
- Physical context: dated USGS, ICSG, exchange-warehouse, customs, producer,
  smelter, and profile-routed news evidence.
- Interpretation: `knowledge/copper/`.

Save an approved Section 64 PDF to
`ccvm/data/products/copper/cme_bulletin/<date>.pdf`. Verify its internal date;
never rename an older bulletin to impersonate a new one.

## Supported validation run

1. Confirm the PDF's internal date and Section 64 identity.
2. Set `CCVM_PRODUCT=copper`.
3. Invoke: **Use `$curvelens-daily-analysis` to run Copper for `<date>`.**
4. Resume durable state; do not use `--restart` unless explicitly requested.
5. Inspect analysis, statistics, mobile, and monitor outputs.
6. Do not prepare notification, mutate an outbox, schedule, or deliver.

## Live-data acceptance gates

1. Verify HG individual symbols against official settlements across several
   dates and contract roll boundaries.
2. Pin official futures and option expiry dates, including holiday cases.
3. Confirm a real Section 64 fixture includes monthly HX and excludes Copper
   weekly option families.
4. Verify $/lb units, strike scaling, $0.0005 premium tick, deltas, and serial
   option-to-HG mappings.
5. Validate curves, IV, greeks, and RND fits for all in-horizon expiries across
   consecutive days. Failed fits remain explicit limitations, not probabilities.
6. Verify every physical or macro value carries source, vintage, covered
   period, geography, units, and cadence.
7. Review the downloader, destination, and disabled cron separately before
   any production approval.

## Copper-specific analytical conventions

- Separate mine, concentrate, smelter, refined, scrap, inventory, and
  fabrication evidence.
- Do not infer China from U.S. industrial proxies.
- Do not equate electrification capacity announcements with immediate Copper
  consumption; preserve project timing and intensity assumptions.
- Do not sum differently defined warehouse series without reconciliation.
- Do not reuse precious-metal monetary logic or oil inventory logic.

## Dashboard and safety

Copper appears automatically in the unified dashboard. The dashboard reads
only `ccvm/data/products/copper/` and starts no workflow or delivery.

Never fabricate data or readiness, commit credentials or runtime data, call a
model API from repository code, enable schedules, or deliver without explicit
approval. Knowledge changes follow `knowledge/MAINTENANCE.md`.

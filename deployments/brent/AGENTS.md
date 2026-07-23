# AGENTS.md — CurveLens ICE Brent Deployment

This is the operational runbook for the experimental ICE Brent deployment.
Follow it together with the repository-level `AGENTS.md`.

## Status and scope

Status: **experimental — validation only**.

Analyze authoritative ICE Futures Europe Brent futures and American-style
options settlements. Do not silently substitute NYMEX Brent contracts,
continuous Yahoo prices, CFDs, or another vendor's close. Intraday analysis,
live scheduling, and delivery are outside initial scope.

Product facts belong in `ccvm/config/markets/brent.yaml`, expiry rules in
`ccvm/src/ccvm/reference/brent_calendar.py`, interpretation in
`knowledge/brent/`, and operating policy here.

## Deployment environment

Before every runtime command:

```bash
export CCVM_PRODUCT=brent
```

Runtime state resolves to `ccvm/data/products/brent/`. Never use another
product's runtime directory.

## Authoritative market-data handoff

ICE settlement data is licensed. This repository does not contain ICE
credentials or make an unreviewed ICE API call. Obtain futures and options
exports through the deployment owner's authorized ICE Data Services, ICE
Connect, API, or bulk-file entitlement; transform them to the canonical JSON
schema; and place them at:

```text
ccvm/data/products/brent/authorized_market_data/trade_date=<date>/futures.json
ccvm/data/products/brent/authorized_market_data/trade_date=<date>/options.json
```

Both top-level documents require:

```json
{"trade_date":"YYYY-MM-DD","exchange":"ICE Futures Europe","product":"B","settlements":[]}
```

Futures rows require `contract_code`, `delivery_month`, and `settlement`;
volume and open interest are optional. Options rows require `option_expiry`,
`underlying_contract`, `underlying_delivery_month`, `strike`, `call_put`, and
`settlement`; bid, ask, volume, open interest, IV, and greeks are optional.
Contract codes use physical `B` plus month letter and two-digit year.

The collector validates product, date, and required fields and then stores
immutable raw copies. It does not treat the manually transformed JSON as an
independent source: retain the original authorized export and transformation
audit outside Git according to the data license.

## Other sources

- Physical fundamentals: EIA weekly petroleum data is U.S./Atlantic context;
  set `EIA_API_KEY`. It is not a complete Brent balance.
- Macro cross-checks: profile-configured FRED series; set `FRED_API_KEY`.
- Regional benchmark: delayed `CL=F` context only, never a synchronized
  executable Brent-WTI spread.
- News: profile-routed EIA, IEA, energy, offshore, tanker, OPEC+, sanctions,
  and refining coverage.
- Positioning: no primary ICE Brent COT collector is approved at bootstrap.
- Interpretation: `knowledge/brent/`.

## Supported validation run

1. Verify the authorized ICE export's settlement date, venue, contract family,
   units, and license.
2. Produce both canonical JSON files at the isolated handoff paths.
3. Invoke: **Use `$curvelens-daily-analysis` to run Brent for `<date>`.**
4. On `NEED_AUTHORIZED_MARKET_DATA`, obtain the missing file; never substitute.
5. Resume durable state and inspect analysis, statistics, mobile, and monitor
   outputs. Do not use `--restart` unless explicitly requested.
6. Do not prepare notification, mutate an outbox, schedule, or deliver.

## Live-data acceptance gates

1. Compare several days of imported futures settlements, volume, and open
   interest with the licensed ICE source and verify contract rolls.
2. Pin official ICE futures and option expiry dates, including Christmas,
   New-Year, Thanksgiving, and bank-holiday cases.
3. Verify option premium semantics under futures-style margining, $0.01 units,
   exercise style, underlying mappings, IV, and greeks.
4. Validate curves and RND fits across every expiry in the rolling 12-month
   horizon. Failed fits remain explicit limitations, not probabilities.
5. Verify the Brent-WTI context is labeled approximate and time-aligned before
   any comparative conclusion.
6. Review all physical series for geography, period, vintage, and cadence.
7. Review the licensed acquisition mechanism, transformation audit,
   destination, disabled cron, and delivery separately.

## Brent-specific analytical conventions

- Separate North Sea/BFOET, OPEC+, Atlantic flows, refining, freight,
  inventories, and global demand.
- Do not treat Cushing stocks as a global Brent balance.
- Distinguish announced targets from observed production and loadings from
  completed exports.
- Do not import WTI curve bands or assume a benchmark spread is synchronized.

## Dashboard and safety

Brent appears automatically in the unified dashboard and reads only its
isolated runtime directory. Never fabricate data or access, commit licensed
exports or credentials, bypass input validation, enable schedules, or deliver
without explicit approval. Knowledge changes follow
`knowledge/MAINTENANCE.md`.

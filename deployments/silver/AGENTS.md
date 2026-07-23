# AGENTS.md — CurveLens Silver Deployment

This is the operational runbook for the experimental COMEX Silver deployment.
Follow it together with the repository-level `AGENTS.md`. Silver uses SI/SO,
CME Section 64, and a combined macro-and-industrial analytical desk.

## Status and scope

Status: **experimental — validation only**.

Work only on settled COMEX Silver futures (`SI`) and monthly Silver options
(`SO`). Weekly options (`SO1`–`SO5`), intraday data, petroleum/crop events, and
live delivery are outside the initial scope. Do not enable cron or Telegram
until a human approves live-data acceptance.

Silver is an implementation of the shared framework, not a Gold fork. Product
facts belong in `ccvm/config/markets/silver.yaml`, expiry rules in
`ccvm/src/ccvm/reference/silver_calendar.py`, interpretation in
`knowledge/silver/`, and operating policy here. Generalizable requirements
belong in shared interfaces with regression coverage for existing products.

## Deployment environment

Before every runtime command, set and verify:

```bash
export CCVM_PRODUCT=silver
```

Run from the repository root. Runtime state automatically resolves to
`ccvm/data/products/silver/`. Never use another product's directory.

## Product sources

- Futures: profile-configured SI individual contracts. Yahoo `.CMX` symbols
  are a bootstrap assumption pending live acceptance.
- Options: CME Section 64 Metals Option Products PDF.
- Positioning: CFTC Silver code `084691`.
- Macro and industrial proxies: profile-configured FRED series. Set
  `FRED_API_KEY`; preserve each series' observation date and cadence.
- Physical/technology context: the Silver knowledge pack, Silver Institute,
  USGS, and dated profile-routed news. There is no automated physical provider
  at bootstrap because public physical releases are slow and USGS monthly
  surveys are currently paused.
- Interpretation: `knowledge/silver/`.

Do not run `agent/event_run.py --event eia` or any USDA event workflow. Until a
Section-64-capable downloader is reviewed, save a human-approved PDF to
`ccvm/data/products/silver/cme_bulletin/<date>.pdf`. Never rename an old PDF to
impersonate a new date.

## Supported validation run

1. Confirm the PDF's internal bulletin date and Section 64 identity.
2. Save it under the isolated Silver bulletin directory.
3. Invoke:

   > Use `$curvelens-daily-analysis` to run Silver for `<date>`.

4. The durable controller reviews QC, runs every role from `silver.yaml`, and
   synthesizes the report. Never use `--restart` unless explicitly requested.
5. Stop and report exact details on `NEED_CME_PDF`,
   `ORCHESTRATION_BLOCKED`, or `ORCHESTRATION_ERROR`.
6. On completion, inspect the primary, statistics, mobile, and monitor outputs.
   Do not invoke notification preparation, mutate an outbox, or deliver.

## Live-data acceptance gates

Before production:

1. Verify SI individual-contract symbols across several settlement dates,
   including the 26-consecutive-month cycle and deferred July/December listings.
2. Pin official CME expiry dates, including holiday cases.
3. Confirm a real Section 64 fixture selects monthly `SO CALL` / `SO PUT` only
   and excludes `SO1`–`SO5`.
4. Verify the bulletin's strike scale, $0.001 option premium tick, deltas,
   expiries, and option-to-SI underlying-month mappings.
5. Validate constrained-fit residuals, fitted mass, forward, tail coverage,
   units, and curve ordering across consecutive days.
6. Confirm every macro/industrial value carries an observation date and
   cadence. Never treat monthly/quarterly proxies or annual forecasts as
   same-day demand.
7. Review downloader, delivery QC, destination, and disabled cron template;
   enable them only with explicit approval.

Workflow completion is not production acceptance. The native-agent workflow is
the supported path; do not make SDK, model HTTP API, `codex exec`, or
vendor-model CLI calls.

## Silver-specific analytical conventions

- Silver has both monetary/investment and industrial drivers. Analyze them
  separately, then report agreement or conflict.
- Solar adoption is not a demand number by itself; track capacity alongside
  silver loading, thrifting, and substitution.
- Much mine supply is a byproduct of lead, zinc, copper, or gold production.
  A Silver price move need not cause a proportional short-run supply response.
- Industrial and physical releases are slower than the settlement market.
  Preserve publication vintage and do not manufacture daily precision.
- Do not reuse Gold volatility bands, WTI inventory logic, or Corn crop logic.

## Dashboard

Silver automatically appears in the shared dashboard after its profile is
installed:

```bash
deployments/run_dashboard.sh
```

Select Silver in the sidebar. It reads only
`ccvm/data/products/silver/` and does not start analysis or delivery.

## Safety

- Never fabricate data, delivery success, or production readiness.
- Never read or write another product's runtime directory or outbox.
- Never commit credentials, PDFs, runtime data, reports, or outbox state.
- Knowledge changes follow `knowledge/MAINTENANCE.md`.

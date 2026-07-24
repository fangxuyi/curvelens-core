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

## Authoritative ICE Report Center sources

Use the official daily ICE Report Center CSV exports:

- Futures: `https://www.ice.com/report/10`, contract `B` (Brent Crude Futures).
- Options: `https://www.ice.com/report/166`, contract `B` (Options on Brent
  Futures).

Use `$curvelens-ice-report-download` when either handoff file is missing. ICE
may require its click-through terms, login, or CAPTCHA. Those are human gates:
never bypass or automate around them. The deployment owner has explicitly
approved one deduplicated `HUMAN_ACTION_REQUIRED` Telegram alert per report page
and trade date. Use the ICE download skill to queue it in the Brent-isolated
outbox, deliver only that exact message through the configured Brent deployment
integration, ack it, and pause for the user. This operational alert does not
authorize daily-report preparation or delivery. Select the requested trade
date explicitly; “latest” is acceptable only when it equals the requested date.

After downloading both files, run:

```bash
CCVM_PRODUCT=brent ccvm/.venv/bin/python \
  ccvm/scripts/import_ice_brent_reports.py \
  --date <YYYY-MM-DD> \
  --futures-csv <report-10.csv> \
  --options-csv <report-166.csv>
```

The importer verifies date and Brent identity, normalizes the official CSV
fields, archives the exact source bytes and hashes under the isolated runtime
directory, and atomically creates:

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

ICE settlement data is licensed. Never commit or redistribute downloaded CSVs,
source manifests, canonical handoffs, credentials, or runtime data. The
repository makes no ICE API call and stores no ICE credential. If browser
access is unavailable, obtain the same reports through an owner-authorized ICE
channel and use the same importer; never transform values by hand.

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

1. Verify both official ICE exports' settlement date, venue, contract B,
   units, and license.
2. Import both CSVs with `$curvelens-ice-report-download`.
3. Invoke: **Use `$curvelens-daily-analysis` to run Brent for `<date>`.**
4. On `NEED_AUTHORIZED_MARKET_DATA`, use the ICE download skill; never
   substitute.
5. Resume durable state and inspect analysis, statistics, mobile, and monitor
   outputs. Do not use `--restart` unless explicitly requested.
6. Do not prepare or deliver the daily report, enable a schedule, or mutate
   other outbox items. Only the human-gate alert above is approved.

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

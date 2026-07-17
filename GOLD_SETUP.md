# Gold Deployment Design

Gold is a product implementation inside CurveLens Core, not a fork. Shared
analytics remain in `ccvm/src/ccvm/`; Gold-specific facts live in four places:

1. `ccvm/config/markets/gold.yaml` — identity, feeds, bulletin layout, serial
   option mapping, COT code, validation bounds, and capability declarations.
2. `ccvm/src/ccvm/reference/gold_calendar.py` — exchange expiry rules.
3. `knowledge/gold/` — reviewed interpretation and event calendar.
4. A deployment environment — `CCVM_PRODUCT=gold`, its own data directory,
   outbox, agent, and disabled-until-validated schedules.

## Why one repository

Framework changes and product implementations should evolve through the same
test suite. A Gold requirement that is genuinely generic (serial option-month
mapping is the first example) is added to the shared profile contract and is
covered for both WTI and Gold. Gold-only policy stays in its profile, calendar,
knowledge pack, or deployment runbook. This avoids both core forks and a core
full of `if product == "gold"` branches.

## Isolation model

Run WTI and Gold from the same commit but in separate processes:

```bash
CCVM_PRODUCT=wti  CCVM_DATA_DIR=/srv/curvelens/wti  ...
CCVM_PRODUCT=gold CCVM_DATA_DIR=/srv/curvelens/gold ...
```

Each deployment must have separate manifests, raw/bronze/silver/gold data,
reports, monitor state, outbox, Telegram destination, and cron jobs. Code and
virtual environment may be shared. Never switch `CCVM_PRODUCT` against an
existing unscoped data directory.

## Gold-specific execution differences

- Download CME Section 64 Metals Option Products, not WTI's Section 63.
- Parse only monthly `OG CALL` / `OG PUT`; weekly `OG1`–`OG5` are out of scope.
- Map serial option months through COMEX Rule 115101; a constant month offset
  is incorrect.
- Do not run EIA event jobs. The initial Gold system has no fundamentals
  provider. COT remains useful through CFTC market code `088691`.
- Do not add a DXY or rates "benchmark spread" until the framework can declare
  sign, units, and transformation. Subtracting unlike prices is meaningless.

## Validation gates before live delivery

1. Verify individual Yahoo contract symbols (`GC*.CMX`) on a real collection
   day; replace the bootstrap feed if coverage is incomplete.
2. Capture a Section 64 PDF and assert monthly OG rows, strikes, deltas, expiry,
   and underlying contracts against the CME bulletin.
3. Pin official option expiration-calendar dates, including holiday cases.
4. Run at least several consecutive settlement days and inspect surface mass,
   model-vs-bulletin delta error, units, and young-history labels.
5. Review `deployments/gold/AGENTS.md`, add a Section-64-capable downloader,
   and approve delivery QC before replacing the intentionally non-executable
   cron placeholder or enabling Telegram.

## Current scope

This branch is a safe scaffold, not a production enablement. It supplies the
profile, calendar, knowledge shape, shared serial-month abstraction, and tests.
It also supplies an experimental deployment identity and validation runbook.
Live downloader configuration, real-PDF fixtures, feed acceptance, schedules,
and delivery remain deliberately disabled pending the validation gates above.

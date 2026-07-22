# AGENTS.md — CurveLens Gold Deployment

This is the operational runbook for the experimental COMEX Gold deployment.
Follow it together with the repository-level `AGENTS.md`. This file overrides
WTI conventions: Gold uses GC/OG, CME Section 64, and no EIA petroleum flow.

## Status and scope

Status: **experimental — validation only**.

Work only on settled COMEX Gold futures (`GC`) and monthly Gold options (`OG`).
Weekly options (`OG1`–`OG5`), intraday data, EIA petroleum events, and live
delivery are outside the initial scope. Do not enable cron or Telegram until a
human approves the live-data acceptance results.

Gold remains an implementation of the shared framework, not a fork. Product
facts belong in `ccvm/config/markets/gold.yaml`, expiry rules in
`ccvm/src/ccvm/reference/gold_calendar.py`, interpretation in `knowledge/gold/`,
and operating policy in this runbook. Requirements that generalize beyond Gold
belong in shared framework interfaces with regression coverage for WTI; avoid
product-name conditionals in shared code.

## Deployment environment

Before every runtime command, set and verify:

```bash
export CCVM_PRODUCT=gold
```

Run commands from the repository root. Runtime state automatically resolves to
`ccvm/data/products/gold/`; never use the WTI directory. `CCVM_DATA_DIR` is an optional
advanced override, not part of fresh setup.

## Product sources

- Futures: profile-configured GC individual contracts. The initial Yahoo
  `.CMX` symbols are a bootstrap assumption pending live acceptance.
- Options: CME Section 64 Metals Option Products PDF from the URL in
  `ccvm/config/markets/gold.yaml`.
- Positioning: CFTC Gold code `088691`.
- News: sources and keywords in the Gold product profile.
- Physical fundamentals: none. Never run `agent/event_run.py --event eia`.
- Macro: profile-configured FRED series (real yields, broad USD, breakevens,
  and Treasury rates). Set `FRED_API_KEY` to collect them; see
  `knowledge/gold/macro.md`. Macro context does not replace live-data acceptance.
- Interpretation: `knowledge/gold/`.

The existing WTI headed downloader is Section-63-specific and must not be used
for Gold. Until a Section-64-capable downloader is reviewed, a human-approved
PDF must be saved to `ccvm/data/products/gold/cme_bulletin/<date>.pdf` before a validation
run. Never rename an old PDF to impersonate a new date.

## Supported validation run

1. Confirm the PDF's internal bulletin date and Section 64 identity.
2. Save it at `ccvm/data/products/gold/cme_bulletin/<date>.pdf`.
3. Invoke:

   > Use `$curvelens-daily-analysis` to run Gold for `<date>`.

4. The skill uses `agent/analysis_orchestrator.py` to start or resume persisted
   state, review QC, fan out every role configured in `gold.yaml`, wait for
   validated results, and synthesize. Never use `--restart` unless the user
   explicitly requests a fresh run.
5. On `NEED_CME_PDF`, `ORCHESTRATION_BLOCKED`, or `ORCHESTRATION_ERROR`, stop
   and report the exact missing input or failed gate. Never use a force-PDF path
   unless a human explicitly approves a futures-only diagnostic.
6. On `ORCHESTRATION_COMPLETE`, inspect the analysis and quality outputs.
   During experimental status, do not invoke `notify.py`, mutate an outbox, or
   send Telegram messages.

## Live-data acceptance gates

All gates must pass before proposing production status:

1. Verify the configured individual GC futures symbols return the required
   curve on multiple settlement days, or replace the bootstrap feed.
2. Pin official CME expiration-browser dates, including holiday cases.
3. Parse a real Section 64 fixture and confirm only monthly `OG CALL` / `OG PUT`
   rows are selected; weekly `OG1`–`OG5` rows must be excluded.
4. Compare strikes, premiums, deltas, option expiries, and underlying GC
   contracts against the visible bulletin.
5. Confirm model-vs-bulletin delta error, RND raw mass, units, curve ordering,
   and young-history labels across several consecutive days.
6. Review a Gold-specific downloader, delivery QC, Telegram destination, and
   disabled cron template; enable them only with explicit approval.

An `ORCHESTRATION_COMPLETE` result proves that the workflow completed; it does
not by itself pass these gates. Production status requires clean diagnostics
over consecutive settlement days and explicit approval of scheduling and
delivery. This agent-orchestrated path is the only supported daily analysis;
do not invoke internal preparation scripts as an alternate workflow. Do not
make direct SDK, HTTP model API, `codex exec`, `claude`, or other vendor-model
CLI calls.

## Gold-specific conventions

- GC represents 100 troy ounces and is quoted in USD per troy ounce.
- Monthly OG options are American-style and exercise into futures.
- Serial option months use the mapping in `gold.yaml`; it is not a constant
  offset. January and February map to February GC, March and April to April,
  and so on.
- Gold has no EIA confirmation signal. Agreement must degrade gracefully to
  the capabilities actually present.
- Do not add a DXY/rates benchmark until the framework can declare direction,
  units, and transformation; subtracting unlike prices is invalid.

## Read-only Q&A

Use `agent/query.py` with the Gold environment variables. Cite metric dates and
gold-layer sources, preserve the settlement-only caveat, consult
`knowledge/gold/`, and refuse intraday or unsupported-product questions. Q&A
never touches the outbox or triggers delivery.

## Dashboard

Gold uses the shared Streamlit dashboard with an explicit product and port so it
does not collide with the WTI dashboard:

```bash
deployments/gold/run_dashboard.sh
```

The launcher runs from `ccvm/`, sets `CCVM_PRODUCT=gold`, and binds Streamlit to
`127.0.0.1:8502`. Do not run Gold on WTI's dashboard port.

## Safety

- Never fabricate data, delivery success, or production readiness.
- Never read or write the WTI deployment's runtime directory.
- Never send Gold messages from the WTI outbox or vice versa.
- Never commit credentials, PDFs, runtime data, reports, or outbox state.
- Knowledge changes follow `knowledge/MAINTENANCE.md` and are reviewed by PR.

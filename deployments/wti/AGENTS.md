# AGENTS.md — CurveLens WTI Deployment

Follow this runbook together with the repository-level `AGENTS.md`. This agent
operates WTI only. It must not read from or write to Gold runtime state.

## Status and scope

The native-agent daily analysis is the supported WTI workflow. It covers
settled NYMEX WTI futures (`CL`), monthly WTI options (`LO`), physical
fundamentals, positioning, product news, and the WTI knowledge pack.

Automatic delivery is a separate capability. Onboarding or completing an
analysis does not authorize a Telegram destination, outbox mutation, or live
schedule. Historical event and delivery utilities remain maintenance tools;
they are not alternate daily-analysis entry points.

## Deployment environment

Every runtime command must explicitly set:

```bash
export CCVM_PRODUCT=wti
```

Run from the repository root. Runtime state resolves to
`ccvm/data/products/wti/`. `CCVM_DATA_DIR` is an advanced migration/storage
override and must never point at another product's directory.

The deployment requires:

- Python 3.12+, the `ccvm/.venv` environment, and `pdftotext`;
- `EIA_API_KEY` in the local environment for weekly petroleum fundamentals;
- a headed-browser method approved for the CME Section 63 bulletin;
- native Codex sub-agent support; no repository model API key is used.

## Product sources

- Futures: profile-configured CL contracts.
- Options: the exact CME Section 63 Energy Options bulletin configured in
  `ccvm/config/markets/wti.yaml`.
- Fundamentals: EIA Weekly Petroleum Status Report data.
- Positioning: CFTC WTI-PHYSICAL code `067651`.
- Relative value: the profile-configured Brent context.
- News and interpretation: the WTI profile and `knowledge/wti/`.

Never substitute a different bulletin, reuse an old PDF under a new name, or
treat settled values as executable quotes.

## Supported daily run

Invoke the repository skill:

> Use `$curvelens-daily-analysis` to run WTI for `<trade-date>`.

The operating agent must:

1. Read root `AGENTS.md` and this file, set `CCVM_PRODUCT=wti`, and verify the
   environment without printing secrets.
2. Acquire the current Section 63 bulletin through the approved headed-browser
   path, verify the date printed inside it, and save it as
   `ccvm/data/products/wti/cme_bulletin/<trade-date>.pdf`.
3. Run the checked-in skill for that bulletin trade date. Do not invoke an
   internal preparation script as a substitute for the skill.
4. Let `agent/analysis_orchestrator.py` start or resume the durable state
   machine. Never use `--restart` unless the user explicitly requests a fresh
   run.
5. Allow the skill to create one QC reviewer, then all profile-configured WTI
   specialists in parallel, and finally the synthesizer.
6. Finish only on `ORCHESTRATION_COMPLETE`, or report the exact
   `NEED_CME_PDF`, `ORCHESTRATION_BLOCKED`, or `ORCHESTRATION_ERROR` detail.
7. Report the final analysis paths and retained limitations. Do not queue or
   deliver the analysis without separate approval.

The WTI profile currently creates these temporary specialist roles:

- `futures_curve` — flat price, curve, carry, positioning, and term structure;
- `vol_surface` — implied volatility, skew, term structure, and RND diagnostics;
- `fundamentals` — inventories, balances, refining, supply, and demand.

Role mandates, evidence sections, news routing, and required checks come from
`ccvm/config/markets/wti.yaml`; do not recreate them in a cron prompt.

## Execution and artifacts

`agent/analysis_orchestrator.py` is the only supported daily controller. It
owns this product-neutral phase graph:

```text
QC_REVIEW_REQUIRED
→ SPECIALISTS_REQUIRED
→ SYNTHESIS_REQUIRED
→ READY_TO_FINALIZE
→ COMPLETE
```

Deterministic collection, normalization, feature calculation, news collection,
and evidence-packet construction run inside preparation. They are necessary
components, not a second operational workflow.

State and outputs are isolated below:

- `ccvm/data/products/wti/analysis_workflow/trade_date=<date>/run.json`
- `ccvm/data/products/wti/analysis_workflow/trade_date=<date>/`
  specialist packets and responses
- `ccvm/data/products/wti/analysis/trade_date=<date>/analysis.{md,json}`
- `ccvm/data/products/wti/quality_reports/<date>.{md,json}`

## Daily scheduling

Use `deployments/wti/cron.example`. It schedules an isolated OpenClaw agent
turn, not Python directly. The retry window is safe because the controller
resumes the date's `run.json` and a completed run returns completion instead of
creating another analysis.

Before enabling the template:

1. replace the checkout path and agent-name placeholders;
2. verify bulletin acquisition and one complete manual daily run;
3. verify product data isolation and runtime permissions;
4. review runtime/usage cost and the schedule window; and
5. obtain explicit approval to create and enable the schedule.

The checked-in template is disabled. Do not add a delivery destination to its
message. Scheduled analysis and scheduled delivery are separate approvals.

## Quality and interpretation rules

- Never fabricate or silently clean observations. Retry only controller-
  allowlisted collection remediation; retain unresolved limitations.
- Treat failed RND diagnostics as limitations, never probabilities.
- Distinguish settlement analytics from executable prices and confirmed
  mispricing.
- Compare physical balances with flat price and curve response rather than
  treating an inventory surprise as mechanically directional.
- Preserve stale-release, thin-history, missing-options, and source failures in
  specialist responses and synthesis.
- Interpret using `knowledge/wti/`; knowledge updates follow
  `knowledge/MAINTENANCE.md`.

## Other supported operations

- Read-only questions: use `agent/query.py` with `CCVM_PRODUCT=wti`; cite dates
  and sources and preserve settlement-only caveats.
- Historical migration: follow `deployments/wti/MIGRATION.md` with both legacy
  and Core schedules stopped.
- Event utilities (`agent/event_run.py`) and `agent/notify.py` are not the daily
  analysis controller. Never use them to bypass the skill or infer permission
  to deliver.
- Dashboard: run `deployments/run_dashboard.sh`, open
  `http://127.0.0.1:8501`, and select WTI in the sidebar. The shared server
  preserves product-isolated runtime paths.

## Safety

- Never modify Gold data, another agent registration, global OpenClaw state, or
  unrelated schedules.
- Never commit credentials, PDFs, runtime data, reports, workflow responses, or
  outbox state.
- Never call a model SDK, HTTP model API, `codex exec`, `claude`, or another
  vendor-model CLI from repository code.
- Never enable a schedule or delivery without explicit approval.

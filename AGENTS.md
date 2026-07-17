# AGENTS.md — CurveLens Project

This repository is the working directory for the `curvelens` OpenClaw agent.
Treat it as a project-scoped WTI analytics repository, not as the main
assistant workspace.

## Scope

Work only on the CurveLens daily WTI futures & options workflow:
- fetching the day's raw inputs (CL futures, CME LO option settlements, EIA
  weeklies, energy RSS news)
- running the deterministic analytics pipeline (normalize → gold features →
  BAW vol surface → cross-market agreement → daily brief)
- delivering the daily brief, and priority alerts on confirmed directional
  agreement, via Telegram

Do not modify the main OpenClaw workspace, global memory, personal
preferences, unrelated repositories, or unrelated cron jobs.

## Runtime Model

Use cron to trigger the `curvelens` agent on a T+1 retry window after CME
settlements have had time to post. Commands are run from the repository root
(`CurveLens/`); the Python interpreter is the pipeline's venv at `ccvm/.venv`.
The agent invokes the tested pipeline (`agent/run_pipeline.py`) as a tool call
rather than reconstructing the collect/normalize/compute/report workflow from a
prompt each run — that part stays fully deterministic and testable.

The agent's own job is narrow but load-bearing in three places the pipeline
**deliberately cannot do itself**:

1. **Fetch the CME option bulletin.** The CME daily bulletin is behind Akamai
   bot protection, so `run_pipeline.py` cannot download it. The agent fetches
   it with its own browser/fetch capability and saves it to the exact path the
   pipeline expects, before running the pipeline.
2. **Deliver via Telegram.** `run_pipeline.py` and `notify.py` never hold a bot
   token and never call the Telegram API. All delivery goes through the agent's
   own Telegram integration.
3. **Judge borderline priority alerts.** The pipeline flags `alert_worthy`
   deterministically; the agent decides whether a marginal trigger is genuinely
   worth interrupting someone, and may hold a borderline alert (the daily brief
   still goes out).

A recurring run is three passes, back-to-back. The cron job fires every 30
minutes across the early-morning retry window regardless of state; the freshness
gate in Pass 1 is what keeps a firing cheap — once that day's bulletin has been
handled, later firings discard the re-download and finish silently without
recomputing or re-sending.

**Pass 1 — fetch the bulletin, and only proceed if it is a new date.**

The CME "current" URL always serves *some* bulletin — whatever was published
most recently. So the download may succeed even before a new trading day is
available; the real question is whether it is a *new* trading day you have not
already handled. Gate on that before running the pipeline.

1. Download the CME Section 63 Energy Options bulletin with the existing headed
   Playwright downloader:
   `CME_BULLETIN_RUN_PIPELINE=0 CME_BULLETIN_DIR=/Users/openclawyi/Desktop/curvelens/ccvm/data/cme_bulletin /Users/openclawyi/.openclaw/workspace/skills/cme-bulletin-downloader/scripts/download_cme_bulletin_pdf.js`.
   The downloader handles CME's browser requirement, extracts the bulletin's
   internal PDF date, saves the PDF under the configured `CME_BULLETIN_DIR`,
   and prints JSON containing `date` and `path`. If the download itself fails
   (URL unreachable / not a PDF / Chrome cannot capture real PDF bytes), finish
   with a concise `CME_PDF_UNAVAILABLE` summary and send nothing; the next
   half-hour cron run will retry.
2. Use the downloader JSON `date` as the authoritative `<pdf-date>` for the
   run, and use the JSON `path` as the saved PDF path.
3. Freshness gate: run
   `ccvm/.venv/bin/python agent/notify.py --is-new <pdf-date>`.
   - If `is_new` is **false** (this date's brief was already delivered):
     run nothing and send nothing. Finish with a concise `CME_PDF_NOT_NEW`
     summary. The bulletin hasn't rolled to a new day yet; the next cron run
     will check again.
   - If `is_new` is **true**: continue to Pass 2 with `<date>` = `<pdf-date>`.

Never fabricate settlements or run with `--force-pdf` unless a human explicitly
approves a futures-only run.

**Pass 2 — run the pipeline to completion.**

4. After the PDF has been saved to `ccvm/data/cme_bulletin/<date>.pdf`, run
   `ccvm/.venv/bin/python agent/run_pipeline.py --date <date>`.
5. On `{"result": "OK", ...}`, the daily brief has been written to `report_md`
   / `report_json`. Note `agreement_state`, `eia_scenario`, and `alert_worthy`.
6. On `{"result": "ERROR", "step": ..., "detail": ...}`, stop and report which
   stage failed. A failed optional stage (catalyst extraction) does not
   error the run; only required stages do.

**Pass 3 — prepare and deliver.**

7. Run `ccvm/.venv/bin/python agent/notify.py --prepare --date <date>`, where
   `<date>` is the PDF date. This
   formats a `DAILY_BRIEF` (always) and, when the day is alert-worthy, a
   `PRIORITY_ALERT`, queueing them in `ccvm/data/agent_outbox/pending.json`. It skips
   any message already queued or already delivered — re-running is safe.
8. Run `ccvm/.venv/bin/python agent/notify.py --list-pending` and read the
   `items` array. Each item has `id`, `type`, and `text`.
9. Deliver each item's `text` **verbatim** (Markdown parse mode) via your
   Telegram integration to the configured chat. Send `PRIORITY_ALERT` items
   immediately; the `DAILY_BRIEF` is the routine digest. Do not rewrite or
   re-summarize the message text.
10. After each successful send, ack it with
   `ccvm/.venv/bin/python agent/notify.py --ack <id>` (or `--ack-all` once
   everything for this run is sent) so it is never delivered twice.

Parse all JSON with Python, not `jq`, so missing/empty keys do not create shell
failures.

## Event-Calendar Runs (same-day mini-runs)

Besides the T+1 settlement run, two scheduled mini-runs react to releases the
same day — no bulletin needed (see `config/cron.example`; schedule sourced
from `knowledge/wti/calendar.yaml`):

- **EIA flash** (Wed ~10:35 ET): `ccvm/.venv/bin/python agent/event_run.py
  --event eia`. Collects the fresh EIA data and queues an `EIA_FLASH` only
  when the seasonally-adjusted trigger confirms (bull/bear) — hours ahead of
  the settlement brief. `QUIET` is the normal outcome; finish silently.
- **COT update** (Fri ~15:35 ET): `--event cot`. Queues a `COT_UPDATE` only on
  a material positioning shift (|WoW| ≥ 20k lots, or 1y percentile ≥90/≤10).

Delivery is identical to the daily run: `notify.py --list-pending`, send the
queued `EIA_FLASH` / `COT_UPDATE` text verbatim, ack each sent id. The
`<date>:<type>` dedup ids guarantee an event message can never double-send,
including against the next morning's settlement run.

## Repository Layout

The repo has two tiers: the **agent layer** at the root (what the cron run
touches) and the **`ccvm/` pipeline package** (the deterministic engine).

Agent layer (repo root):
- `agent/run_pipeline.py` — single-entry orchestrator (5 stages → one JSON line)
- `agent/notify.py` — formats + queues Telegram messages; ack after send
- `AGENTS.md`, `IDENTITY.md` — this spec + agent identity
- `config/cron.example` — agent-driven cron template
- `knowledge/wti/` — the WTI knowledge pack (see Knowledge section below)

Pipeline package (`ccvm/`):
- `ccvm/.venv/` — the Python environment all commands run under
- `ccvm/scripts/collect_day.py` — raw ingest (futures / CME PDF / EIA / RSS)
- `ccvm/scripts/normalize_day.py` — raw → bronze → silver + quality report
- `ccvm/scripts/compute_features.py` — silver → gold (curve, BAW vol surface, agreement)
- `ccvm/scripts/extract_catalysts.py` — RSS → ranked catalyst events (needs `claude` CLI)
- `ccvm/scripts/generate_report.py` — gold → `ccvm/data/reports/<date>.md` + `.json`
- `ccvm/src/ccvm/` — the analytics package (collectors, normalizers, analytics, reporting)
- `ccvm/config/sources.yaml` — configured RSS/EIA sources
- `ccvm/config/markets/wti.yaml` — WTI contract/market config
- `ccvm/.env` — `EIA_API_KEY` (gitignored; see `ccvm/.env.example`)
- `ccvm/app/dashboard.py` — Streamlit terminal (separate, not part of the cron run)

Runtime state (under `ccvm/data/`, gitignored):
- `ccvm/data/cme_bulletin/<date>.pdf` — the agent-downloaded CME bulletin
- `ccvm/data/reports/<date>.{md,json}` — the daily brief
- `ccvm/data/agent_outbox/pending.json` — messages awaiting Telegram delivery + ack
- `ccvm/data/agent_outbox/delivered.json` — delivery log (dedupe guarantee)

## Knowledge

`knowledge/wti/` is your domain reference — consult it whenever you compose
narrative, judge a borderline alert, or answer a question:

- `conventions.md` — contract specs, the verified expiry rules (code source of
  truth: `ccvm/src/ccvm/reference/wti_calendar.py`), settlement methodology,
  bulletin quirks (absolute put deltas, cent-strikes, label conventions)
- `calendar.yaml` — scheduled releases (EIA Wed 10:30 ET, API Tue, COT Fri,
  rig count Fri, monthly MOMR/OMR/STEO). Machine-read by the pipeline: the
  brief's "Next Review" section is generated from it.
- `regimes.md` — what "normal" looks like for WTI vol, skew, and curve shape.
  Read it alongside the brief's Context percentiles: percentiles say "unusual
  vs recent history," regimes.md says "unusual vs the market's character"
  (e.g. WTI is normally put-skewed — a sustained positive 25Δ RR is itself a
  signal worth a sentence).
- `seasonality.md` — how to read the same EIA print in different months
- `analogs.md` — dated historical episodes for "biggest since X" comparisons

How the pack is generated and maintained — provenance tiers, per-file
volatility models, the authored→measured migration, and the monthly review
ritual — is defined in `knowledge/MAINTENANCE.md`. Follow it for any pack
change you propose.

Rules:
- **Cite the pack** (file name) when your narrative leans on it. Do not
  assert market lore from memory when the pack covers the topic; if the pack
  is wrong or stale, that is a correction to propose, not to improvise around.
- **Propose knowledge edits via PR only** — never edit the pack silently.
  New dated events (e.g. an announced OPEC+ meeting) go in `calendar.yaml`'s
  `dated:` list the same way.
- Before delivering, sanity-check the brief against the pack and the Context
  block: if a headline number sits at an extreme percentile or contradicts a
  regime norm, your delivery message may prepend **one short narrative
  sentence** flagging it — the pipeline-formatted numbers themselves stay
  verbatim. (The fuller pre-send QC protocol is tracked separately, issue #45.)

## On-Demand Q&A

When a user asks you a question directly (Telegram or otherwise), you are the
analyst answering from this system's own data — never from memory alone.

**Answer protocol:**
1. Query the gold layer through the read-only toolkit — do not hand-roll data
   access:
   - `ccvm/.venv/bin/python agent/query.py series --metric rr25 --days 30`
     (metrics: front_settle, curve_slope, m1_m2_spread, atm_iv, rr25, bf25,
     skew_slope, realized_vol_10d, vrp_10d, brent_wti_spread)
   - `... query.py curve|surface [--date D]` — a date's curve / vol surface
   - `... query.py state` — scenario states, streaks, scorecard
   - `... query.py detail --kind oi|cot|rnd|triggers|eia_seasonal [--date D]`
   - `... query.py sql --query "SELECT ..."` — DuckDB over gold parquet views
     (futures_features, option_features, eia_features, history_context);
     SELECT-only, mutating keywords are blocked
2. Consult `knowledge/wti/` for interpretation (regimes, seasonality,
   analogs) and **cite the file** when your answer leans on it.
3. **Cite numbers with their dates and sources** ("25Δ RR +4.3% on 2026-07-10,
   gold/option_features"). Preserve the settlement-only caveat when the
   question is about tradability.
4. **Refuse what the data cannot support** — intraday questions, products not
   collected, forecasts beyond what the RND/scenarios express. Say what you
   *can* answer instead.
5. Keep answers short: the number, the context (percentile / regime norm),
   one line of interpretation. Link the day's brief for depth.

Q&A never touches the outbox, never triggers alerts, and never mutates data —
`query.py` is read-only by construction.

## Pre-Send QC Gate

Before delivering a `DAILY_BRIEF`, read it. Spend one minute as an editor,
not a courier:

1. **Structural sanity** — all sections present? If the brief is visibly
   broken (missing sections, empty tables, unrendered placeholders), **hold
   delivery** and report the failure instead of shipping a damaged brief.
2. **Consistency vs yesterday** — does the "What Changed" block agree with
   the numbers in Section 1? A settle that moved +$3 with an unchanged
   30-day band, or an agreement transition the diff doesn't mention, means
   something upstream broke — hold and report.
3. **Anomaly scan** — check the Context percentiles and the Calibration
   Scorecard against `knowledge/wti/regimes.md`. If a headline number sits
   at an extreme (≥90th/≤10th percentile) or contradicts a regime norm, you
   may prepend **one short narrative sentence** to the delivery message
   citing the pack (e.g. "Note: 25Δ RR at its 91st %ile — WTI is normally
   put-skewed; see regimes.md").
4. **Numbers stay verbatim** — you may add the one sentence above; you may
   never edit, round, or re-derive a number in the brief itself.
5. **Alert judgment** — for a `PRIORITY_ALERT`, apply the Alert Policy
   below; a held alert must be noted in your run summary with the reason.

## Alert Policy

The daily brief is always delivered. A `PRIORITY_ALERT` is queued only when the
day is `alert_worthy`, meaning either:
- the cross-market agreement state is `confirmed_upside_risk` or
  `confirmed_downside_risk` (futures and options both signal the same
  direction), or
- the EIA scenario trigger is `bull_confirmed` or `bear_confirmed` (a
  >3M bbl draw or >4M bbl build).

Never deliver a duplicate. A given date can queue each message type at most
once (ids are `<date>:<type>`), and every sent message must be acked so
`pending.json` never re-offers it. No-data or futures-only days still deliver a
brief; they simply won't carry a priority alert.

The agent may hold a borderline `PRIORITY_ALERT` if its own read is that the
trigger is marginal — but it must still deliver the `DAILY_BRIEF`, and should
note in its run summary that an alert was held and why.

## Safety Rules

- Do not modify files outside this project's repository root.
- `agent/run_pipeline.py` and `agent/notify.py` must never call the Telegram
  API or hold a bot token / chat ID. All delivery goes through the agent's own
  Telegram integration.
- The agent must not send Telegram messages except for pending `DAILY_BRIEF` /
  `PRIORITY_ALERT` items in `ccvm/data/agent_outbox/pending.json`, or an explicit
  approved test.
- Never fabricate market data. If the CME bulletin cannot be fetched, report the
  failure — do not invent settlements or run `--force-pdf` without human
  approval.
- Do not change global OpenClaw config, identity, memory, or unrelated cron jobs
  unless explicitly asked.
- `EIA_API_KEY` lives in `ccvm/.env` (gitignored). Never commit secrets.
- Ask before enabling live Telegram delivery schedules.

## Data Integrity

- Settlement data only. The brief describes the settled curve and vol surface;
  it does not establish executability or confirmed mispricing.
- BAW (Barone-Adesi & Whaley) is the primary IV model — WTI LO options are
  American. Deep-OTM early-exercise premia carry known approximation error and
  are filtered out of the surface metrics.
- EIA weeklies lag by up to a week; the brief labels the report period.

## Imported Claude Cowork project instructions

framework work

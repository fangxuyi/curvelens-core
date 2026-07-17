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

## Deployment environment

Before every runtime command, set and verify:

```bash
export CCVM_PRODUCT=gold
export CCVM_DATA_DIR=/absolute/path/to/gold-data
```

Run commands from the repository root. The data directory must be dedicated to
Gold and must not be the WTI directory or the legacy `ccvm/data` directory.

## Product sources

- Futures: profile-configured GC individual contracts. The initial Yahoo
  `.CMX` symbols are a bootstrap assumption pending live acceptance.
- Options: CME Section 64 Metals Option Products PDF from the URL in
  `ccvm/config/markets/gold.yaml`.
- Positioning: CFTC Gold code `088691`.
- News: sources and keywords in the Gold product profile.
- Fundamentals: none initially. Never run `agent/event_run.py --event eia`.
- Interpretation: `knowledge/gold/`.

The existing WTI headed downloader is Section-63-specific and must not be used
for Gold. Until a Section-64-capable downloader is reviewed, a human-approved
PDF must be saved to `$CCVM_DATA_DIR/cme_bulletin/<date>.pdf` before a validation
run. Never rename an old PDF to impersonate a new date.

## Validation run

1. Confirm the PDF's internal bulletin date and Section 64 identity.
2. Save it at `$CCVM_DATA_DIR/cme_bulletin/<date>.pdf`.
3. Run:

   ```bash
   ccvm/.venv/bin/python agent/run_pipeline.py --date <date>
   ```

4. On `NEED_CME_PDF` or `ERROR`, stop and report the exact missing input/stage.
   Never use `--force-pdf` unless a human explicitly approves a futures-only
   diagnostic.
5. On `OK`, inspect the report and quality outputs. During experimental status,
   do not run `notify.py --prepare` against a live delivery outbox and do not
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

## Safety

- Never fabricate data, delivery success, or production readiness.
- Never read or write the WTI deployment's runtime directory.
- Never send Gold messages from the WTI outbox or vice versa.
- Never commit credentials, PDFs, runtime data, reports, or outbox state.
- Knowledge changes follow `knowledge/MAINTENANCE.md` and are reviewed by PR.

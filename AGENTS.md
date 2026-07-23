# AGENTS.md — CurveLens Core Framework

This repository is the shared CurveLens framework. It contains reusable market
analytics plus product implementations such as WTI, Gold, and Corn. It is not itself
a single-product operational deployment.

## Instruction precedence

All agents follow this file. A product deployment agent must additionally read
exactly one deployment runbook:

- `deployments/wti/AGENTS.md`
- `deployments/gold/AGENTS.md`
- `deployments/corn/AGENTS.md`
- `deployments/silver/AGENTS.md`
- `deployments/corn/AGENTS.md`

The deployment runbook is authoritative for product-specific collection,
schedules, QC, and delivery. `CCVM_PRODUCT` selects which runbook applies. If a
runtime task does not name a product, establish it before proceeding.

## Agent onboarding

On first activation in a fresh clone:

1. Establish exactly one product from the user's instruction; do not operate a
   runtime task with the implicit default.
2. Read this file and exactly one matching `deployments/<product>/AGENTS.md`.
3. Verify Python 3.12+, `ccvm/.venv`, installed requirements, `pdftotext`, and
   the product's required data-provider keys without printing secrets.
4. Run the shared tests before proposing operational use. Perform only the
   validation or analysis the user requested; never infer approval for a cron,
   live delivery, destination, or production-status change.
5. Use `$curvelens-daily-analysis` for every daily analysis. The
   checked-in skill and product profile define the roles; do not reconstruct or
   hardcode product prompts from memory, and do not bypass it with a script-only
   daily workflow.

The supported registration sentence is “Operate the CurveLens `<product>`
deployment.” for WTI, Gold, or Corn. A single runtime agent operates one
product; separate product agents may share the checkout.

## Runtime isolation

Every runtime command must set the product explicitly:

```bash
CCVM_PRODUCT=<product>
```

Runtime state is automatically isolated under `ccvm/data/products/<product>/`;
WTI, Gold, and Corn therefore coexist safely in one clone. `CCVM_DATA_DIR` is an
optional advanced override for migrations or external storage. Never configure
two products with the same override. Delivery destinations, agent
registrations, and cron jobs remain product-specific; code and virtual
environment are shared.

## Framework boundaries

- Shared behavior belongs in `ccvm/src/ccvm/` and must be driven by product
  profiles or capability interfaces, not `if product == ...` branches.
- Product facts belong in `ccvm/config/markets/<product>.yaml`, the product
  calendar module, `knowledge/<product>/`, or `deployments/<product>/`.
- A product-specific requirement that generalizes cleanly should improve the
  shared interface and retain regression coverage for existing products.
- Keep WTI, Gold, and Corn tests green for framework changes.
- Settlement analytics describe settled markets; they do not establish
  executability or confirmed mispricing.

## Operational safety

- Never fabricate market data or silently substitute another product's feed.
- Bulletin-backed products require the exact configured bulletin and date.
- `agent/notify.py` never holds delivery credentials or calls Telegram directly.
  Delivery uses the deployment agent integration.
- Send only messages queued by the selected deployment's isolated outbox, and
  ack every successful send to preserve deduplication.
- Never enable schedules or live delivery without explicit approval.
- Do not commit `.env`, API keys, chat IDs, runtime data, outbox state, or other
  secrets.
- Do not modify global OpenClaw state, unrelated repositories, or unrelated
  schedules.

## Knowledge maintenance

Interpretation must use the active product's knowledge pack. Follow
`knowledge/MAINTENANCE.md`; knowledge changes are proposed through pull
requests and retain provenance, review dates, and mechanical guardrails.

## Development verification

Run the shared suite from `ccvm/`:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

For a product change, also run a smoke check with explicit `CCVM_PRODUCT`. Do
not enable a product deployment until its runbook's acceptance gates pass.

## Daily analysis workflow

Use the repository skill `$curvelens-daily-analysis` for the daily workflow.
`agent/analysis_orchestrator.py` is the only supported daily controller and
persists and enforces the
product-neutral phase graph; deterministic code prepares evidence and validates
outputs, while the host Codex framework natively delegates QC, every
profile-configured specialist role, and synthesis. Do not call model SDKs, HTTP
model APIs, `codex exec`, or vendor model CLIs from repository code.

The controller must emit a synthesis action only after every configured role
validates. The removed `agent/run_pipeline.py` path must not be recreated or
used as an alternate daily workflow. Automatic delivery remains a separate
deployment capability and requires explicit approval; completing analysis does
not authorize queuing or sending it.

The controller's `inspect` command and product-isolated `workflow_monitor.md`,
`workflow_monitor.json`, and `workflow_events.jsonl` are the supported debugging
surface. They record assigned inputs, submitted outputs, validation failures,
and phase transitions without exposing private chain-of-thought or introducing
model calls. `analysis.md` is the primary report and must integrate validated
numbers, driver/news assessment, conflicts, and forward watch items; the
separate `statistics.md` remains an audit supplement. `mobile.md` is a
deterministic phone-first rendering of the same validated synthesis. When
delivery has been separately approved, notification preparation must use this
mobile rendering rather than inventing or re-summarizing the report.

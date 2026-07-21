# AGENTS.md — CurveLens Core Framework

This repository is the shared CurveLens framework. It contains reusable market
analytics plus product implementations such as WTI and Gold. It is not itself
a single-product operational deployment.

## Instruction precedence

All agents follow this file. A product deployment agent must additionally read
exactly one deployment runbook:

- `deployments/wti/AGENTS.md`
- `deployments/gold/AGENTS.md`

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
5. Use `$curvelens-daily-analysis` for native multi-specialist analysis. The
   checked-in skill and product profile define the roles; do not reconstruct or
   hardcode Gold/WTI prompts from memory.

The supported registration sentences remain “Operate the CurveLens WTI
deployment.” and “Operate the CurveLens Gold deployment.” A single runtime
agent operates one product; separate product agents may share the checkout.

## Runtime isolation

Every runtime command must set the product explicitly:

```bash
CCVM_PRODUCT=<product>
```

Runtime state is automatically isolated under `ccvm/data/products/<product>/`;
WTI and Gold therefore coexist safely in one clone. `CCVM_DATA_DIR` is an
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
- Keep WTI and Gold tests green for framework changes.
- Settlement analytics describe settled markets; they do not establish
  executability or confirmed mispricing.

## Operational safety

- Never fabricate market data or silently substitute another product's feed.
- Bulletin-backed products require the exact configured bulletin and date.
- `agent/run_pipeline.py` and `agent/notify.py` never hold delivery credentials
  or call Telegram directly. Delivery uses the deployment agent integration.
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

## Agent-framework analysis workflow

Use the repository skill `$curvelens-daily-analysis` for the analyst-style
shadow workflow. `agent/analysis_orchestrator.py` persists and enforces the
product-neutral phase graph; deterministic code prepares evidence and validates
outputs, while the host Codex framework natively delegates QC, every
profile-configured specialist role, and synthesis. Do not call model SDKs, HTTP
model APIs, `codex exec`, or vendor model CLIs from repository code.

The controller must emit a synthesis action only after every configured role
validates. Until a deployment runbook explicitly promotes this path, outputs
are shadow artifacts only: do not queue or deliver them.

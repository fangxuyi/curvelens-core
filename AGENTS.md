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
schedules, QC, and delivery. If no deployment is named, do not infer WTI from
the framework's backward-compatible code default; establish the intended
product before performing runtime operations.

## Runtime isolation

Every runtime command must set both variables explicitly:

```bash
CCVM_PRODUCT=<product>
CCVM_DATA_DIR=<absolute product-specific data directory>
```

Never point two products at the same data directory. Each product requires its
own raw/bronze/silver/gold data, manifests, reports, monitor state, outbox,
delivery destination, agent registration, and cron jobs. Code and virtual
environment may be shared.

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

For a product change, also run a smoke check with explicit `CCVM_PRODUCT` and a
temporary isolated `CCVM_DATA_DIR`. Do not enable a product deployment until
its runbook's acceptance gates pass.

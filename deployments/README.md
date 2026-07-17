# CurveLens Deployments

The repository is shared; runtime agents are product-specific. Register one
agent per deployment and give it this instruction, substituting the product:

```text
You are a CurveLens product deployment agent.

Read the repository-level AGENTS.md, then read exactly one deployment runbook:
deployments/<product>/AGENTS.md

That runbook is authoritative for product collection, QC, schedules, and
delivery. Before any runtime command set CCVM_PRODUCT=<product> and set
CCVM_DATA_DIR to this deployment's absolute isolated data directory. Never use
another product's data directory or silently rely on the default product.
Never enable schedules or live delivery without explicit approval.
```

Use separate OpenClaw agent registrations, data directories, outboxes, Telegram
destinations, and cron sets for WTI and Gold. They may share this checkout,
commit, Python environment, and framework tests.

| Deployment | Runbook | Status |
|---|---|---|
| WTI | `deployments/wti/AGENTS.md` | operational |
| Gold | `deployments/gold/AGENTS.md` | experimental; validation only |

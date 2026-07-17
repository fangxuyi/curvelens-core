# CurveLens Deployments

The repository is shared; runtime agents select one product. Register an agent
with the repository root as its working directory and use one short instruction:

```text
Operate the CurveLens <product> deployment. Read AGENTS.md and
deployments/<product>/AGENTS.md. Set CCVM_PRODUCT=<product> for every runtime
operation and follow that runbook. Do not enable schedules or delivery without
explicit approval.
```

That is the entire registration instruction. Data automatically goes to
`ccvm/data/<product>/`, so no data path needs to be chosen on a fresh install.
WTI and Gold share the checkout, environment, and tests. Their state, outboxes,
Telegram destinations, and schedules remain separate.

| Product | Agent instruction | Status |
|---|---|---|
| WTI | `Operate the CurveLens wti deployment...` | operational |
| Gold | `Operate the CurveLens gold deployment...` | experimental; validation only |

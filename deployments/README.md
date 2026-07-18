# CurveLens Deployments

The repository is shared; each runtime agent selects one product. Set the
repository root as the agent's working directory and give it one sentence:

- WTI: **Operate the CurveLens WTI deployment.**
- Gold: **Operate the CurveLens Gold deployment.**

That is the entire registration instruction. The repository-level `AGENTS.md`
routes the agent to the matching product runbook and runtime profile. Data goes to
`ccvm/data/products/<product>/`, so no data path needs to be chosen on a fresh
install. WTI and Gold share the checkout, environment, and tests. Their state,
outboxes, Telegram destinations, and schedules remain separate.

| Product | Status |
|---|---|
| WTI | Operational |
| Gold | Validation-only; schedules and delivery disabled |

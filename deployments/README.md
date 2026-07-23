# CurveLens Deployments

The repository is shared; each runtime agent selects one product. Set the
repository root as the agent's working directory and give it one sentence:

- WTI: **Operate the CurveLens WTI deployment.**
- Gold: **Operate the CurveLens Gold deployment.**
- Corn: **Operate the CurveLens Corn deployment.**
- Silver: **Operate the CurveLens Silver deployment.**

That is the entire registration instruction. The repository-level `AGENTS.md`
routes the agent to the matching product runbook and runtime profile. Data goes to
`ccvm/data/products/<product>/`, so no data path needs to be chosen on a fresh
install. All products share the checkout, environment, and tests. Their state,
outboxes, Telegram destinations, and schedules remain separate.

For the supported daily analysis, the one-sentence instructions are:

- WTI: **Use `$curvelens-daily-analysis` to run WTI for today.**
- Gold: **Use `$curvelens-daily-analysis` to run Gold for today.**
- Corn: **Use `$curvelens-daily-analysis` to run Corn for today.**
- Silver: **Use `$curvelens-daily-analysis` to run Silver for today.**

On first activation the agent verifies the installation and tests, establishes
the product explicitly, and does not enable schedules or delivery. Native Codex
subagents perform QC, the profile-configured desks, and synthesis; no separate
model API key or repository model client is used. Daily schedules must invoke
this skill through an isolated agent turn. The old script-only daily entry point
has been removed.

| Product | Status |
|---|---|
| WTI | Operational |
| Gold | Validation-only; schedules and delivery disabled |
| Corn | Validation-only; schedules and delivery disabled |
| Silver | Validation-only; schedules and delivery disabled |

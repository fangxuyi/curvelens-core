# CurveLens Deployments

The repository is shared; each runtime agent selects one product. Set the
repository root as the agent's working directory and give it one sentence:

- WTI: **Operate the CurveLens WTI deployment.**
- Brent: **Operate the CurveLens Brent deployment.**
- Gold: **Operate the CurveLens Gold deployment.**
- Copper: **Operate the CurveLens Copper deployment.**
- Corn: **Operate the CurveLens Corn deployment.**
- Silver: **Operate the CurveLens Silver deployment.**
- S&P 500: **Operate the CurveLens S&P 500 deployment.**
- Nasdaq-100: **Operate the CurveLens Nasdaq-100 deployment.**
- Russell 2000: **Operate the CurveLens Russell 2000 deployment.**

That is the entire registration instruction. The repository-level `AGENTS.md`
routes the agent to the matching product runbook and runtime profile. Data goes to
`ccvm/data/products/<product>/`, so no data path needs to be chosen on a fresh
install. All products share the checkout, environment, and tests. Their state,
outboxes, Telegram destinations, and schedules remain separate.

For the supported daily analysis, the one-sentence instructions are:

- WTI: **Use `$curvelens-daily-analysis` to run WTI for today.**
- Brent: **Use `$curvelens-daily-analysis` to run Brent for today.**
- Gold: **Use `$curvelens-daily-analysis` to run Gold for today.**
- Copper: **Use `$curvelens-daily-analysis` to run Copper for today.**
- Corn: **Use `$curvelens-daily-analysis` to run Corn for today.**
- Silver: **Use `$curvelens-daily-analysis` to run Silver for today.**
- S&P 500: **Use `$curvelens-daily-analysis` to run S&P 500 for today.**
- Nasdaq-100: **Use `$curvelens-daily-analysis` to run Nasdaq-100 for today.**
- Russell 2000: **Use `$curvelens-daily-analysis` to run Russell 2000 for today.**

On first activation the agent verifies the installation and tests, establishes
the product explicitly, and does not enable schedules or delivery. Native Codex
subagents perform QC, the profile-configured desks, and synthesis; no separate
model API key or repository model client is used. Daily schedules must invoke
this skill through an isolated agent turn. The old script-only daily entry point
has been removed.

| Product | Status |
|---|---|
| WTI | Operational |
| Brent | Validation-only; authorized ICE data required; schedules and delivery disabled |
| Gold | Validation-only; schedules and delivery disabled |
| Copper | Validation-only; schedules and delivery disabled |
| Corn | Validation-only; schedules and delivery disabled |
| Silver | Validation-only; schedules and delivery disabled |
| S&P 500 | Validation-only; authorized CME data required; schedules and delivery disabled |
| Nasdaq-100 | Validation-only; authorized CME data required; schedules and delivery disabled |
| Russell 2000 | Validation-only; authorized CME data required; schedules and delivery disabled |

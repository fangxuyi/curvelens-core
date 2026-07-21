# CurveLens Core

CurveLens Core is one shared framework for running daily futures and options
monitoring across multiple products. Clone the repository once, then give each
product its own agent.

## Simple deployment

Set the repository root as the agent's working directory and give it one
sentence:

- WTI: **Operate the CurveLens WTI deployment.**
- Gold: **Operate the CurveLens Gold deployment.**

For the shadow multi-specialist report, say: **Use
`$curvelens-daily-analysis` to run Gold (or WTI) for today.**

The repository instructions select the correct product profile and runbook.
Both agents share the code and Python environment while keeping their runtime
state, schedules, outboxes, and delivery destinations separate:

```text
WTI  -> ccvm/data/products/wti/
Gold -> ccvm/data/products/gold/
```

WTI is operational. Gold is currently validation-only and must pass the live
Section 64 options-data acceptance gates before its schedule or delivery is
enabled.

## Documentation

- [Install and extend the framework](SETUP.md)
- [Product deployment status](deployments/README.md)
- [WTI operating runbook](deployments/wti/AGENTS.md)
- [Migrate an existing WTI deployment](deployments/wti/MIGRATION.md)
- [Gold operating runbook](deployments/gold/AGENTS.md)
- [Multi-agent analysis workflow proposal](docs/ANALYSIS_WORKFLOW.md)

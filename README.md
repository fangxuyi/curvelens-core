# CurveLens Core

CurveLens Core is one shared futures-and-options analytics framework. WTI and
Gold are product configurations in the same repository; they share code and a
Python environment while keeping data, workflow state, schedules, outboxes,
and delivery destinations isolated.

## Fresh installation

Prerequisites:

- Python 3.12 or newer;
- Poppler/`pdftotext` for CME bulletin parsing (`brew install poppler` on macOS);
- a Codex/OpenClaw agent environment for native sub-agent analysis and optional
  delivery integration.

Install and verify:

```bash
git clone https://github.com/fangxuyi/curvelens-core.git
cd curvelens-core
python3 -m venv ccvm/.venv
ccvm/.venv/bin/pip install -r ccvm/requirements.txt
cp ccvm/.env.example ccvm/.env
cd ccvm && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

Fill only the data-provider keys needed by the product in `ccvm/.env`: WTI
uses `EIA_API_KEY`; Gold macro collection uses `FRED_API_KEY`. Model API keys,
Telegram tokens, and chat IDs do not belong in the repository.

## Onboard an agent

Set the repository root as the agent's working directory and give it exactly
one product sentence:

- **Operate the CurveLens WTI deployment.**
- **Operate the CurveLens Gold deployment.**

The root `AGENTS.md` makes the agent establish `CCVM_PRODUCT`, read exactly one
deployment runbook, verify the environment, and preserve product isolation. Do
not combine both products in one runtime agent; use one agent registration per
product, while both registrations may share this checkout and virtualenv.

For the multi-specialist shadow analysis, say:

- **Use `$curvelens-daily-analysis` to run WTI for today.**
- **Use `$curvelens-daily-analysis` to run Gold for today.**

Codex then natively delegates data-quality review, every specialist role in the
active product profile, and synthesis. Repository code makes no model API or
vendor-model CLI calls.

## Runtime isolation

Always select the product explicitly:

```text
CCVM_PRODUCT=wti  -> ccvm/data/products/wti/
CCVM_PRODUCT=gold -> ccvm/data/products/gold/
```

`CCVM_DATA_DIR` is an advanced override for migrations or external storage.
Never point two products at the same override.

| Product | Specialist desks | Deployment status |
|---|---|---|
| WTI | Futures curve, volatility surface, physical fundamentals | Operational deterministic brief; multi-agent analysis remains shadow-only |
| Gold | Futures curve, volatility surface, macro | Validation-only; schedules and delivery disabled |

Bulletin-backed runs require the exact CME bulletin and trade date. Follow the
selected deployment runbook for download, freshness, acceptance, scheduling,
and delivery policy. A successful shadow analysis does not authorize delivery.

## Documentation

- [Framework setup and extension guide](SETUP.md)
- [Agent registration and deployment status](deployments/README.md)
- [WTI operating runbook](deployments/wti/AGENTS.md)
- [WTI history migration](deployments/wti/MIGRATION.md)
- [Gold operating runbook](deployments/gold/AGENTS.md)
- [Multi-agent analysis workflow](docs/ANALYSIS_WORKFLOW.md)

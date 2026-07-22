# CurveLens Core

CurveLens Core is a shared futures-and-options analysis framework. WTI, Gold,
and Corn are product configurations in one repository: they share code and a Python
environment, while market data, workflow state, schedules, and delivery state
remain isolated by product.

The supported daily workflow is agent-orchestrated. Deterministic Python code
collects and checks data, computes market features, prepares evidence packets,
persists workflow state, and validates the final report. A Codex operating
agent creates temporary native sub-agents for data-quality review, each
profile-configured specialist desk, and final synthesis. Repository code does
not call a model API, model SDK, or vendor-model CLI.

## Install

Prerequisites:

- Python 3.12 or newer;
- Poppler/`pdftotext` for CME bulletin parsing (`brew install poppler` on macOS);
- a Codex/OpenClaw environment that supports repository skills and native
  sub-agents;
- headed-browser access for protected CME bulletins;
- `EIA_API_KEY` for WTI fundamentals, `FRED_API_KEY` for macro data, or
  `USDA_NASS_API_KEY` for Corn crop observations.

```bash
git clone https://github.com/fangxuyi/curvelens-core.git
cd curvelens-core
python3 -m venv ccvm/.venv
ccvm/.venv/bin/pip install -r ccvm/requirements.txt
cp ccvm/.env.example ccvm/.env
cd ccvm && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

Put provider keys in `ccvm/.env`. Do not put model credentials, Telegram
tokens, chat IDs, or other delivery secrets in the repository. The native
sub-agents use the operating Codex environment, so this project needs no model
API key.

## Onboard one operating agent

Set the repository root as the agent's working directory and register it with
one sentence:

- **Operate the CurveLens WTI deployment.**
- **Operate the CurveLens Gold deployment.**
- **Operate the CurveLens Corn deployment.**

That sentence is intentionally sufficient. On first activation, `AGENTS.md`
requires the agent to select the product, read exactly one product runbook,
verify the environment without printing secrets, run the tests, and use the
checked-in daily-analysis skill. Use one operating-agent registration per
product; all product agents may share the clone and virtual environment.

The operating agent needs:

- read/write access to this checkout and its selected product data directory;
- permission to run the repository Python environment and native sub-agents;
- access to the product's provider keys through the local environment;
- the product-approved CME bulletin acquisition method;
- an explicit date or permission to determine the latest bulletin trade date.

It does not need a separately installed orchestration framework or a direct
OpenAI/Anthropic API credential.

## Run the daily analysis

Give the registered product agent one sentence:

- **Use `$curvelens-daily-analysis` to run WTI for today.**
- **Use `$curvelens-daily-analysis` to run Gold for today.**
- **Use `$curvelens-daily-analysis` to run Corn for today.**

`$curvelens-daily-analysis` is a repository skill invocation, not a shell
variable. For bulletin-backed runs, use the date printed inside the approved
bulletin when it differs from the calendar date.

The execution flow is:

```text
deterministic collection and calculations
                  ↓
       Codex data-quality reviewer
                  ↓
 profile-configured specialists in parallel
                  ↓
           Codex synthesizer
                  ↓
 deterministic validation and daily report
```

WTI configures futures-curve, volatility-surface, and physical-fundamentals
desks. Gold configures futures-curve, volatility-surface, and macro desks. The
Corn profile configures futures-curve, volatility-surface, and crop-fundamentals
desks. The specialists are temporary native Codex sub-agents created for the run; their
validated responses and the controller's `run.json` remain on disk so an
interrupted run can resume.

Each specialist response is mechanically required to include profile-defined
key metrics with an exact value, unit, dated or historical comparison, plain-
English meaning, and evidence IDs. The final report begins with a six-to-ten
item numerical market snapshot, followed by a plain-English summary and the
futures, options, and macro/fundamentals desk detail. Product profiles decide
which measures are mandatory. Young local history is disclosed once; it does
not excuse omitting current market numbers, and a proxy benchmark must always
be labeled as a proxy.

`agent/analysis_orchestrator.py` is the only supported daily-analysis
controller. The former script-only `agent/run_pipeline.py` entry point has been
removed. The controller still invokes deterministic scripts internally to
prepare reproducible evidence; those scripts are not an alternate analysis
workflow.

## Set up a daily schedule

Schedule an isolated turn of the registered product agent—never a bare Python
command. Start from `deployments/<product>/cron.example`, replace its checkout
path and agent name, and keep it disabled until its product runbook's data and
delivery gates have passed and a human explicitly approves enabling it.

The scheduled message must tell the agent to:

1. work from this repository and read the root plus selected product runbook;
2. set `CCVM_PRODUCT` on every runtime command;
3. acquire and verify the exact product bulletin and its internal trade date;
4. invoke `$curvelens-daily-analysis` for that date;
5. resume persisted state instead of restarting an existing run;
6. report completion or the exact blocker; and
7. avoid delivery unless that deployment has separate explicit approval.

The WTI template includes a disabled retry-window schedule. Gold remains
validation-only, so its template is deliberately non-executable. Creating or
enabling either schedule changes external state and is never performed merely
because an agent was onboarded.

## Runtime isolation

Every runtime command selects the product explicitly:

```text
CCVM_PRODUCT=wti  -> ccvm/data/products/wti/
CCVM_PRODUCT=gold -> ccvm/data/products/gold/
CCVM_PRODUCT=corn -> ccvm/data/products/corn/
```

`CCVM_DATA_DIR` is an advanced override for migrations or external storage.
Never configure two products with the same override.

| Product | Specialist desks | Status |
|---|---|---|
| WTI | Futures curve, volatility surface, physical fundamentals | Agent-orchestrated daily analysis supported; automatic delivery separately controlled |
| Gold | Futures curve, volatility surface, macro | Validation-only; schedules and delivery disabled |
| Corn | Futures curve, volatility surface, crop fundamentals | Validation-only; schedules and delivery disabled |

## Documentation

- [Framework setup and extension guide](SETUP.md)
- [Agent registration and deployment status](deployments/README.md)
- [WTI operating runbook](deployments/wti/AGENTS.md)
- [WTI history migration](deployments/wti/MIGRATION.md)
- [Gold operating runbook](deployments/gold/AGENTS.md)
- [Corn operating runbook](deployments/corn/AGENTS.md)
- [Orchestration design and state machine](docs/ANALYSIS_WORKFLOW.md)

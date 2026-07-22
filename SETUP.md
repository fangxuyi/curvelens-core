# CurveLens Core — Framework Setup Guide

*How to stand up a deployment, and what porting to a new commodity requires.*

One repository supports **many products**. Each running agent selects one of
them. The engine (collectors, normalizers, BAW/RND analytics, trigger state
machine, scenario engine, reporting, agent layer) is product-agnostic;
everything product-specific is declared in a **product profile** and its
companion artifacts. `CCVM_PRODUCT` selects the profile (default `wti`).

```
curvelens-core/
├── AGENTS.md / SOUL.md / IDENTITY.md / HEARTBEAT.md   shared rules + identity
├── .agents/skills/curvelens-daily-analysis/           native-agent workflow
├── .codex/agents/                                    generic specialist types
├── agent/            analysis controller · evidence preparation · notify · query
├── deployments/<product>/          product runbook + cron template
├── knowledge/<pack>/               ← product knowledge pack + MAINTENANCE.md
└── ccvm/                            the deterministic engine
    ├── scripts/                     deterministic collection/analytics stages
    ├── config/markets/<product>.yaml  ← product profile (load-bearing)
    ├── src/ccvm/                    package (reference/product.py = profile loader)
    └── data/products/<product>/     isolated runtime state (gitignored)
```

---

## 1. Machine setup (any deployment)

**Prerequisites**
- Python ≥ 3.12
- `poppler` (`brew install poppler`) — the bulletin parser shells out to `pdftotext`
- A Codex/OpenClaw agent environment — native subagents run QC, specialist
  analysis, and synthesis without a repository model client or model API key
- For live delivery only, a configured agent delivery integration; the
  pipeline never holds a bot token (see AGENTS.md Safety Rules)
- The headed-Playwright CME downloader skill (for bulletin products) — CME is
  Akamai-protected; a plain HTTP client cannot fetch the bulletin

**Install**
```bash
git clone https://github.com/fangxuyi/curvelens-core.git && cd curvelens-core
python3 -m venv ccvm/.venv
ccvm/.venv/bin/pip install -r ccvm/requirements.txt
cp ccvm/.env.example ccvm/.env        # fill in EIA_API_KEY (or your provider's key)
export CCVM_PRODUCT=wti               # deployment's product; always explicit
```

That is the only product-specific setup required. Runtime state automatically
resolves to `ccvm/data/products/wti/` (or `ccvm/data/products/gold/`).
`CCVM_DATA_DIR` is an optional advanced override for migration or external
storage.

> Upgrading an older WTI installation whose state is directly under
> `ccvm/data/`? Temporarily set `CCVM_DATA_DIR="$PWD/ccvm/data"` until that state
> is moved into `ccvm/data/products/wti/`. This avoids losing delivery dedup
> history.

**Smoke test**
```bash
cd ccvm && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

**First daily analysis**: activate the registered product agent and say “Use
`$curvelens-daily-analysis` to run `<product>` for `YYYY-MM-DD`.” Bulletin
products require the correctly dated PDF; the controller returns
`NEED_CME_PDF` and its required path when it is absent. The skill then runs the
durable QC → specialists → synthesis flow and reports the final analysis paths.
Do not invoke the internal preparation scripts as an alternative daily flow.

**Agent + schedules**: register a separate OpenClaw agent for each product.
Give it the onboarding instruction in `deployments/README.md`; it must read the
root framework rules plus exactly one `deployments/<product>/AGENTS.md`.
Adapt only that deployment's cron template. A daily schedule must trigger an
agent turn that invokes `$curvelens-daily-analysis`, not a bare Python command.
Templates ship disabled; enabling a schedule or delivery requires separate
explicit approval. Delivery/dedup state lives below the automatically isolated
`ccvm/data/products/<product>/agent_outbox/`.

---

## 2. Porting to a new commodity — the five artifacts

The shared engine has no WTI market defaults other than selecting the `wti`
deployment when `CCVM_PRODUCT` is unset. A port authors these:

### 2.1 Product profile — `ccvm/config/markets/<product>.yaml`

Loaded by `ccvm/src/ccvm/reference/product.py` (`get_product()`). Field
reference (all under the top-level `market:` mapping):

| Field | Example (WTI) | Consumed by |
|---|---|---|
| `name` / `display_name` | `WTI Crude Oil` / `WTI` | extractor prompt, report copy |
| `exchange`, `product_code`, `currency`, `price_unit` | NYMEX, CL, USD, USD/BBL | metadata, report |
| `contract_multiplier`, `tick_size` | 1000, 0.01 | quality checks |
| `futures_prefix` | `CL` | contract codes, ticker construction, silver parsing |
| `options_prefix` | `LO` | option symbols |
| `yfinance_contract_suffix` | `.NYM` | yfinance collector (`CLQ26.NYM`) |
| `month_codes` | `F:1 … Z:12` | code ↔ month mapping |
| `calendar_module` | `ccvm.reference.wti_calendar` | expiry rules everywhere (§2.2) |
| `knowledge_pack` | `wti` | `knowledge/<pack>/` resolution |
| `fundamentals_provider` | `eia_weekly_petroleum` | fundamentals registry (§2.3); omit for none |
| `futures_depth`, `options_expiry_depth` | 12, 5 | collection scope |
| `settlement_min`, `settlement_max` | 1, 500 | product-scale silver validation |
| `options.risk_free_rate` | 0.05 | BAW/Black-76/RND rate assumption |
| `options.premium_tick_size` | 0.10 for Gold | scales the RND convex-repair diagnostic |
| `options.rnd_max_projection_ticks` | 2.0 | maximum tick-bounded repair before RND is rejected |
| `bulletin.product_header_call/put` | `LO CALL` / `LO PUT` | PDF section detection |
| `bulletin.url` | CME Section 63 URL | agent preflight/download instructions |
| `bulletin.strike_scale` | `100` (cents → $) | strike conversion — **verify per product!** |
| `bulletin.underlying_month_offset` | `1` (AUG label → Sep contract) | label → underlying mapping |
| `bulletin.underlying_month_map` | Gold serial-month map | non-linear option month → futures mapping; use instead of offset |
| `bulletin.expiry_basis` | `underlying_month` or `option_month` | selects the calendar rule input |
| `benchmark` | Brent / `BZ=F` | optional relative-value context; omit for none |
| `news.keywords`, `news.sources` | energy terms/feeds | product-scoped catalyst collection; omit for none |
| `cot` | CFTC code/label | optional positioning context; omit for none |
| `scenario/threshold knobs` | — | not needed: thresholds are price-relative (E2) and shocks σ-based (E3) — they self-calibrate |

### 2.2 Calendar module — expiry rules with exchange-verified fixtures

**The highest-stakes artifact.** Wrong expiry rules silently bias every IV,
delta, and expected move (this framework's own history: the WTI 3rd-Friday bug).
The discipline, copied from `wti_calendar.py` + `test_expiry_calendar.py`:

1. Implement `futures_last_trade_date(y, m)` and `option_expiry_date(y, m)`
   using the exchange's published rule (business-day-aware via
   `exchange_calendar.py`, which already covers CME holidays).
2. **Pin 12–24 externally verified dates** in a fixture JSON — exchange
   calendar pages, or a mirroring venue (WTI used the ICE schedule). Include
   holiday-crossing months (Christmas, Good Friday).
3. Fixture-driven tests must pass before anything downstream is trusted.
4. Reference the module in the profile's `calendar_module`.

### 2.3 Fundamentals provider — `ccvm/src/ccvm/fundamentals/`

Register a `FundamentalsProvider` (collector class + bronze/silver/features
modules + `source_id_fragment` + cadence note) in `_REGISTRY`, and name it in
the profile. Examples: WTI → EIA Weekly Petroleum; Henry Hub → EIA Weekly NG
Storage (strong seasonality — the seasonal-band machinery is already generic);
metals → **omit the field**: the pipeline runs fundamentals-less and the
agreement classifier degrades gracefully.

### 2.4 Knowledge pack — `knowledge/<pack>/`

The five-file shape (see `knowledge/MAINTENANCE.md` for the process —
provenance tiers, Last-reviewed headers, stale-date guardrails are enforced by
tests):

- `conventions.md` — specs, expiry rules (pointing at the calendar module as
  code truth), data-source quirks
- `calendar.yaml` — scheduled releases (drives the brief's Next Review and the
  event-calendar runs)
- `regimes.md` — vol/skew/curve norms for THIS product (labeled authored-prior
  until recalibrated from accumulated history)
- `seasonality.md` — how to read the same print in different months
- `analogs.md` — dated episodes, mechanism-first

Also configure `news` and `analysis.roles` in the product profile. The native
agent workflow routes evidence to those roles and instantiates the same generic
specialist agent once per configured desk.

### 2.5 Deployment runbook — `deployments/<product>/`

Create a product-scoped `AGENTS.md` and a disabled `cron.example`. The runbook
declares exact environment variables, bulletin/downloader behavior, supported
event mini-runs, QC gates, delivery policy, and maturity status. The root
identity files are shared; do not duplicate them for each product.

An agent registration must instruct the agent to read root `AGENTS.md` and
exactly one deployment runbook. Never put product schedules or delivery policy
back into root instructions.

---

## 3. Port validation protocol

1. `pytest` green, including the new calendar fixture tests
2. One real day end-to-end through `$curvelens-daily-analysis`; inspect the
   deterministic evidence and synthesized analysis for unit sanity (price
   scale, strike scale!)
3. **Delta check** in the brief's caveats (model delta vs venue-published
   delta) — the empirical detector for wrong TTE/strike-scale/model setup;
   mean |diff| should be ~0.01 or better
4. `raw_mass` diagnostic in the RND block near 1.0 → strike grid parsed sanely
5. Run several consecutive days before enabling delivery: the monitor layers
   (percentiles, streaks, state machine, scorecard) need history and their
   guards label young-history output
6. Only then: register crons, fill chat id, enable deliberately

## 4. Running two products

Two deployments share one checkout and venv, but each has its own
`CCVM_PRODUCT`, OpenClaw agent, cron set, and delivery destination. The
framework automatically isolates all runtime state:

```text
CCVM_PRODUCT=wti  -> ccvm/data/products/wti/
CCVM_PRODUCT=gold -> ccvm/data/products/gold/
```

That includes manifests, market data, reports, monitor state, and outboxes.
Use `CCVM_DATA_DIR` only when deliberately migrating existing state or storing
data outside the checkout; never point two products at the same override.

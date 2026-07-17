# CurveLens Core — Framework Setup Guide

*How to stand up a deployment, and what porting to a new commodity requires.*

One deployment = **one product**. The engine (collectors, normalizers, BAW/RND
analytics, trigger state machine, scenario engine, reporting, agent layer) is
product-agnostic; everything product-specific is declared in a **product
profile** and its companion artifacts. `CCVM_PRODUCT` selects the profile
(default `wti`).

```
curvelens-core/
├── AGENTS.md / SOUL.md / IDENTITY.md / HEARTBEAT.md   agent spec + identity
├── agent/            run_pipeline.py · notify.py · event_run.py · query.py
├── config/cron.example             OpenClaw cron templates (ship --disabled)
├── knowledge/<pack>/               ← product knowledge pack + MAINTENANCE.md
└── ccvm/                            the deterministic engine
    ├── scripts/                     5 pipeline stages
    ├── config/markets/<product>.yaml  ← product profile (load-bearing)
    ├── src/ccvm/                    package (reference/product.py = profile loader)
    └── data/                        runtime state (gitignored)
```

---

## 1. Machine setup (any deployment)

**Prerequisites**
- Python ≥ 3.12
- `poppler` (`brew install poppler`) — the bulletin parser shells out to `pdftotext`
- `claude` CLI on PATH (Claude Code, OAuth session) — catalyst extraction; the
  pipeline treats this stage as optional and degrades without it
- An OpenClaw install with a Telegram channel — delivery is the **agent's**
  integration; the pipeline never holds a bot token (see AGENTS.md Safety Rules)
- The headed-Playwright CME downloader skill (for bulletin products) — CME is
  Akamai-protected; a plain HTTP client cannot fetch the bulletin

**Install**
```bash
git clone https://github.com/fangxuyi/curvelens-core.git && cd curvelens-core
python3 -m venv ccvm/.venv
ccvm/.venv/bin/pip install -r ccvm/requirements.txt python-dotenv feedparser httpx
cp ccvm/.env.example ccvm/.env        # fill in EIA_API_KEY (or your provider's key)
export CCVM_PRODUCT=wti               # deployment's product (default wti)
```

**Smoke test**
```bash
cd ccvm && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

**First day of data** (bulletin products: PDF must be on disk first —
production gets it via the agent's Playwright downloader):
```bash
ccvm/.venv/bin/python agent/run_pipeline.py --date YYYY-MM-DD
# → NEED_CME_PDF (fetch the bulletin)  |  OK (brief written)  |  ERROR <stage>
```

**Agent + schedules**: register the OpenClaw agent with this repo as its
working directory, then adapt `config/cron.example` — daily T+1 settlement run
(half-hourly retry window + `notify.py --is-new` freshness gate), plus the
event-calendar runs (EIA flash, COT update). Templates ship `--disabled` with a
`telegram:YOUR_CHAT_ID` placeholder: fill the real chat id **at registration
time only** — never commit one. Delivery/dedup state lives in
`ccvm/data/agent_outbox/` (gitignored).

---

## 2. Porting to a new commodity — the four artifacts

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

Also configure `news` in the product profile and author a product event
taxonomy if catalyst extraction is wanted (the extractor prompt is templated
from the profile).

---

## 3. Port validation protocol

1. `pytest` green, including the new calendar fixture tests
2. One real day end-to-end: collect → normalize → compute → report; inspect
   the brief for unit sanity (price scale, strike scale!)
3. **Delta check** in the brief's caveats (model delta vs venue-published
   delta) — the empirical detector for wrong TTE/strike-scale/model setup;
   mean |diff| should be ~0.01 or better
4. `raw_mass` diagnostic in the RND block near 1.0 → strike grid parsed sanely
5. Run several consecutive days before enabling delivery: the monitor layers
   (percentiles, streaks, state machine, scorecard) need history and their
   guards label young-history output
6. Only then: register crons, fill chat id, enable deliberately

## 4. Running two products

Two deployments may share one checkout and venv, but must use separate
`CCVM_PRODUCT`, `CCVM_DATA_DIR`, OpenClaw agents, and cron sets. No runtime
state is shared: manifests, market data, reports, monitor state, and outbox all
resolve below `CCVM_DATA_DIR`. If the variable is omitted, the backward-
compatible default remains `ccvm/data`. Never point two products at that same
directory. (Two energy products may each download the same Section-63 bulletin
PDF into their own data root; harmless.)

# Knowledge Pack — Generation & Maintenance Process

*Process file for `knowledge/<product>/`. The pack's failure mode is silent rot
— nothing crashes when a regime norm drifts stale. This file defines where
knowledge comes from, who may change it, and how staleness is caught.*

## 1. Principles

1. **Pointer-shaped, not duplicative.** Facts that code owns stay in code and
   tests; the pack points at them. Example: expiry rules live in
   `ccvm/src/ccvm/reference/wti_calendar.py` + pinned fixtures —
   `conventions.md` cites them, it does not restate them independently.
2. **Executable where possible.** Any knowledge claim that *can* be a test
   *should* be one (expiry dates → fixture tests; calendar parses → loader
   tests). Prose is for judgment that can't be executed.
3. **Provenance is labeled.** Every file states what kind of knowledge it
   holds (see §2) so a reader knows how much to trust it and how to verify.
4. **PR-only changes.** The agent proposes, a human approves. Never edit the
   pack silently — including "obvious" fixes.

## 2. How knowledge is generated — four provenance tiers

| Tier | Source | Example | Trust / verification |
|---|---|---|---|
| **Verified-external** | exchange schedules, official publications, pinned fixtures | expiry rules (verified vs ICE schedule 2026-07-10, `tests/fixtures/cme_expiry_calendar.json`) | highest — enforced by tests |
| **Measured-internal** | the system's own gold history (`gold/history_context`) | percentile bands, "3rd consecutive draw" | high — recomputed daily; the pack should *converge toward* this tier (§4) |
| **Authored priors** | model/desk knowledge at authoring time | regimes.md vol bands, seasonality windows | medium — labeled "as-remembered, verify before quoting precise figures" |
| **Agent-proposed** | news, catalysts, observed data during operation | a newly announced OPEC+ meeting date; a regime band contradicted by 60 days of data | pending until human-merged; proposals must cite evidence |

## 3. Per-file maintenance model (sorted by volatility)

| File | Volatility | Model |
|---|---|---|
| `calendar.yaml` `dated:` | **high** | active add-and-expire. Agent proposes announced events (OPEC+ meetings etc.) via PR; the loader **warns on past-dated entries** — remove them when the warning fires |
| `calendar.yaml` recurring | low | near-static. Known gotcha: **EIA/API releases shift +1 day on federal-holiday weeks** — until encoded in the loader, the agent flags holiday weeks manually |
| `analogs.md` | append-only | never rewrite history, only append new dated episodes (incl. from this system's own gold history). Keep the mechanism-first framing |
| `regimes.md` | slow drift | recalibrate from measured data (§4); note calibration date |
| `seasonality.md` | stable | annual glance; update the "Status note" when pipeline features (e.g. B4 seasonal triggers) supersede manual guidance |
| `conventions.md` | near-static | changes only on exchange rule changes; corrections carry a dated **History note** (see the expiry-restatement note as the template) |

Every file carries a `*Last reviewed: YYYY-MM-DD*` header — bump it on review
**even when nothing changed**, so staleness is visible at a glance.

## 4. The authored → measured migration

`regimes.md` bands start as authored priors. Once `gold/history_context`
holds ~252 trading days, recalibrate: replace authored bands with measured
percentile bands from the system's own history, note the calibration date and
window, and move the file's provenance label from "authored" toward
"measured". Only genuinely external knowledge (exchange mechanics, historical
episodes) should remain authored long-term. Each file states which pipeline
feature supersedes its manual guidance, so knowledge and code co-evolve
without contradiction.

## 5. Mechanical guardrails (enforced)

- `tests/test_knowledge_loader.py` asserts: calendar parses, prose files
  exist, **`Last reviewed` headers present**, and **no stale `dated:` entries**
  relative to today.
- The loader logs a warning at runtime when a `dated:` event is in the past.
- Expiry claims are enforced by the calendar fixture tests (Tier 1).

## 6. Review cadence

**Monthly ritual** (suits a low-cost agent cron task later):
1. `dated:` entries — remove past events, add newly announced ones
2. Verify next month's option expiry / futures LTD against the exchange
   calendar; extend fixtures if a new year comes into view
3. Check regimes.md against the live Context percentiles — propose
   recalibration if 60+ days contradict a stated band
4. Bump `Last reviewed` headers on everything examined

**Event-driven**: corrections (a wrong convention, a rule change) ship as
their own PR with a dated History note in the affected file.

## 7. Multi-product template

New products get `knowledge/<product>/` with the same five-file shape
(conventions, calendar.yaml, regimes, seasonality, analogs) and the same
tiers. Authoring the pack is most of the porting work for a knowledgeable
agent — this process file is product-agnostic and applies unchanged.

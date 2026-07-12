# WTI Conventions — Contract Specs, Expiry Rules, Data Quirks

*Knowledge pack for the CurveLens agent. Cite this file when explaining
conventions; propose corrections via PR only.*

## Contracts

| | Futures (CL) | Options (LO) |
|---|---|---|
| Exchange | NYMEX (CME Group) | NYMEX |
| Unit | 1,000 bbl | 1 CL futures contract |
| Quote | USD/bbl, tick $0.01 | USD/bbl premium |
| Style | physically delivered at Cushing, OK | **American**, exercises into CL |
| Months | all 12 (F G H J K M N Q U V X Z) | all 12 |

## Expiry rules — single source of truth is `ccvm/src/ccvm/reference/wti_calendar.py`

- **CL futures LTD**: 3 business days before the 25th calendar day of the month
  prior to delivery (if the 25th is not a business day, 3 business days before
  the last business day preceding it).
- **LO option expiry**: futures LTD − 3 business days.
- Business days exclude CME full-closure holidays
  (`ccvm/src/ccvm/reference/exchange_calendar.py`).

Verified 2026-07-10 against the ICE WTI American options schedule (which
mirrors NYMEX LO) and the documented April-2020 dates; pinned in
`ccvm/tests/fixtures/cme_expiry_calendar.json`. **History note:** before
2026-07-10 the pipeline used a 3rd-Friday approximation — IV/greeks history
was restated when this was corrected (front-expiry IVs rose ~1 vol pt).

## CME daily bulletin (Section 63) quirks — learned the hard way

- **Labels**: bulletin expiry labels are the *option expiry month* ("AUG26");
  the underlying is the **next** calendar month's CL ("AUG26" → CLU26).
- **Strikes are in cents**: `6850` = $68.50.
- **Put deltas are printed as absolute values** — the collector negates them
  to the signed convention.
- The "current" bulletin URL always serves the most recent bulletin — the
  internal date, not the URL, says which trade date it is.
- Settlement prices are CME committee/model marks. Deep-ITM calls and OTM puts
  at the same strike can imply IVs ~0.3–3pp apart because calls and puts are
  settled independently — this is why the vol surface is built **OTM-only**.

## Pricing conventions in this system

- **BAW (Barone-Adesi & Whaley 1987)** is the primary IV/greeks model — LO is
  American. Black-76 is retained as a European reference only.
- Deep-OTM early-exercise premia carry known BAW approximation error; the EEP
  table is filtered to ±2σ√T of the forward.
- Daily validation: model delta vs CME-published bulletin delta
  (`delta_check` in the brief's caveats; typically mean|diff| < 0.01).

## Units in the brief

- EIA stocks are in **MBBL = thousand barrels** (EIA convention). A "3,775
  MBBL draw" is what a desk calls "a 3.8 million barrel draw" — prefer the
  desk phrasing in narrative text.
- `crude_draw` fields store the *negated* WoW change (positive = stocks fell).

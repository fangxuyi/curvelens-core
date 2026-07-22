# AGENTS.md — CurveLens Corn Deployment

This is the operating runbook for the experimental CBOT Corn deployment. Follow
it together with the repository-level `AGENTS.md`. Corn uses ZC/OZC, CME Daily
Bulletin Section 56, grain price notation, and USDA crop data.

## Status and scope

Status: **experimental — validation only**.

Work only on settled CBOT Corn futures (`ZC`) and standard or serial Corn
options (`OZC`). Weekly options, intraday data, cash basis, and live delivery
are outside the initial scope. Do not enable a schedule or delivery until a
human approves the live-data acceptance results.

Corn is a product implementation of the shared framework, not a fork. Product
facts belong in `ccvm/config/markets/corn.yaml`, expiry rules in
`ccvm/src/ccvm/reference/corn_calendar.py`, interpretation in `knowledge/corn/`,
and operating policy here.

## Deployment environment

Before every runtime command, set and verify:

```bash
export CCVM_PRODUCT=corn
```

Runtime state resolves to `ccvm/data/products/corn/`. Never use another
product's directory or share a `CCVM_DATA_DIR` override.

## Product sources

- Futures: profile-generated ZC individual contracts. Yahoo `.CBT` symbols are
  a delayed bootstrap source pending live acceptance.
- Options: the exact CME Section 56 Corn/Oat/Rough Rice Options PDF configured
  in `corn.yaml`. Save a human-verified copy as
  `ccvm/data/products/corn/cme_bulletin/<date>.pdf`.
- Crop fundamentals: USDA NASS Quick Stats. Set `USDA_NASS_API_KEY`; a missing
  key is reported as a skipped capability, never replaced with invented data.
- Positioning: CFTC Corn contract market code `002602`.
- Macro: profile-configured FRED context. Set `FRED_API_KEY` when used.
- News and interpretation: the Corn profile and `knowledge/corn/`.

The initial profile does not yet collect WASDE balance sheets, Export Sales,
ethanol production and stocks, gridded weather, or cash basis/freight. Analysts
must identify those gaps and must not imply those inputs were checked.

## Supported validation run

1. Confirm the PDF's internal trade date and Section 56 identity.
2. Save it at the product-isolated bulletin path above.
3. Invoke: **Use `$curvelens-daily-analysis` to run Corn for `<date>`.**
4. Resume persisted state unless the user explicitly requests a restart.
5. Stop on a missing bulletin or blocked quality gate and report the exact
   missing input. Do not force a futures-only result without approval.
6. Inspect the analysis and quality outputs. Do not send or schedule during
   experimental status.

## Live-data acceptance gates

1. Verify the ZC individual-contract feed across several settlement days and
   confirm cents-per-bushel values normalize to USD per bushel.
2. Pin futures last-trade and option-expiry dates, including holiday cases,
   against official CME data.
3. Parse real Section 56 fixtures and compare calls, puts, serial expiries,
   strikes, grain-eighth premiums, deltas, open interest, and underlying ZC
   mappings with the visible bulletin.
4. Review curve ordering and the old-crop/new-crop spreads across crop-year
   transitions.
5. Validate USDA observation labels, units, dates, revisions, and growing-season
   gaps against Quick Stats output.
6. Review RND diagnostics over consecutive days; probabilities remain invalid
   when repair exceeds the profile's tick-bounded limit.
7. Add and review a Section 56 downloader, delivery destination, and disabled
   schedule before proposing production status.

## Corn-specific conventions

- ZC represents 5,000 bushels and CME quotes futures in cents per bushel; the
  framework stores USD per bushel.
- Standard futures months are March, May, July, September, and December.
- Section 56 option premiums use cents and eighths of a cent; the parser
  normalizes them to USD per bushel.
- Serial options map to the next configured underlying futures month. Do not
  infer the underlying by a fixed month offset.
- A carrying futures curve is not a cash-basis signal. Separate futures carry,
  storage economics, local basis, freight, and physical availability.
- Crop condition is descriptive evidence, not a standalone yield forecast.

## Safety

Never fabricate missing USDA, weather, export, ethanol, basis, or bulletin
data. Never commit keys, PDFs, runtime state, reports, or delivery credentials.
This native-agent workflow is the only supported daily-analysis path; do not
make direct model SDK or vendor CLI calls.

# Silver Industrial and Physical Fundamentals

*Last reviewed: 2026-07-23 — provenance: verified USGS and Silver Institute sources plus authored collection policy*

Silver needs more fundamental context than Gold, but the highest-quality public
physical data are slower than a daily futures report. The initial deployment
therefore has no deterministic physical provider. It uses dated official or
industry evidence without forward-filling it as current flow data.

## What matters

- Industrial demand: electronics and electrical applications, photovoltaics,
  brazing/alloys, catalysts, and other fabrication.
- Investment demand: bars, coins, exchange-traded products, futures/options
  positioning, and macro sensitivity.
- Supply: primary Silver mines; byproduct output from lead, zinc, copper, and
  gold operations; recycling; and producer hedging.
- Technology: adoption and production volumes must be separated from silver
  intensity. Solar growth can coincide with thrifting or substitution, so
  “more capacity” is not automatically proportional Silver demand.

The [USGS Silver statistics page](https://www.usgs.gov/centers/national-minerals-information-center/silver-statistics-and-information)
is the preferred official source for U.S. production, trade, consumption, and
annual mineral summaries. USGS states that monthly Mineral Industry Surveys
are paused during its ScienceBase transition, so the workflow must not pretend
that an unchanged table is a fresh daily observation.

The [Silver Institute 2026 outlook](https://silverinstitute.org/silver-market-deficit-forecast-to-continue-in-2026/)
forecasts a sixth consecutive annual market deficit, roughly 820 million ounces
of mine production, and about 650 million ounces of industrial fabrication.
It also forecasts industrial fabrication down about 2% as photovoltaic
thrifting and substitution offset installation growth. These are dated annual
forecast anchors—not daily measurements and not permanent regime thresholds.

## Collection policy

For each release, store the source, publication date, covered period, vintage,
units, geography, and whether it is a forecast or observation. Prefer USGS,
government energy/technology agencies, issuer filings, and the Silver
Institute's survey work. Use news to detect new releases or technology changes,
then compare them with measured SI/SO behavior; do not let keyword routing turn
an unrelated technology article into a Silver driver.

Future automation should be added only after the source offers stable machine
access. Useful candidates are monthly mine/recycling/trade series, solar
installation plus silver-intensity estimates, electronics production, and
holdings/flow data with explicit coverage and publication timestamps.

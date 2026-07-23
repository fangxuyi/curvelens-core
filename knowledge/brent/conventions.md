# Brent Conventions

*Last reviewed: 2026-07-23 — provenance: verified external*

- Product: ICE Futures Europe Brent Crude futures and American-style options,
  physical contract symbol `B` and logical/symbol code `BRN`.
- Contract unit: 1,000 barrels; quotation and minimum fluctuation: $0.01/bbl.
- Futures are deliverable by EFP with an option to cash settle against the ICE
  Brent Index.
- Futures cease on the last ICE Business Day of the second month before the
  contract month, with the Christmas/New-Year adjustment.
- American options exercise into the corresponding Brent future and cease
  three ICE Business Days before that future, with additional Christmas,
  New-Year, and U.S. Thanksgiving adjustments.
- Options use futures-style daily margining. Treat imported settlement values
  as premium values in $/bbl and verify the vendor export definition.
- Code source of truth: `ccvm/src/ccvm/reference/brent_calendar.py`; official
  date source of truth: the ICE futures and options expiry tables.
- Official sources: [ICE Brent Futures](https://www.ice.com/products/219/Brent-Crude-Futures),
  [ICE Brent futures expiries](https://www.ice.com/products/219/Brent-Crude-Futures/expiry),
  [ICE Brent American options](https://www.ice.com/products/218/Brent-Crude),
  and [ICE option expiries](https://www.ice.com/products/218/Brent-Crude-American-style-Options/expiry).

Settlement-only caveat: the system describes settled curves and option
surfaces; it does not establish current executability or confirmed mispricing.

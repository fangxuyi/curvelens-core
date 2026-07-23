# Copper Conventions

*Last reviewed: 2026-07-23 — provenance: verified external*

- Product: COMEX Copper futures (`HG`) and monthly Copper options (`HX`).
- Contract unit: 25,000 pounds; quotation: USD per pound.
- HG and HX minimum price fluctuation is $0.0005/lb, or $12.50 per contract.
- HG futures terminate on the third-last business day of the delivery month.
- HG lists 24 consecutive monthly contracts plus March, May, July, September,
  and December within the nearest 60 months.
- Monthly HX options are American-style and expire four business days before
  the end of the preceding month, subject to the Friday/pre-holiday adjustment.
- JAN/FEB/MAR options exercise into MAR HG; APR/MAY into MAY; JUN/JUL into JUL;
  AUG/SEP into SEP; and OCT/NOV/DEC into DEC.
- Code source of truth: `ccvm/src/ccvm/reference/copper_calendar.py` and
  `ccvm/config/markets/copper.yaml`.
- Official sources: [COMEX Chapter 111](https://www.cmegroup.com/rulebook/COMEX/1a/111.pdf),
  [COMEX Chapter 117](https://www.cmegroup.com/rulebook/COMEX/1a/117.pdf),
  [CME Copper specifications](https://www.cmegroup.com/markets/metals/base/copper.contractSpecs.html),
  and CME Section 64 Metals Option Products bulletins.

Settlement-only caveat: the system describes settled curves and option
surfaces; it does not establish current executability or confirmed mispricing.

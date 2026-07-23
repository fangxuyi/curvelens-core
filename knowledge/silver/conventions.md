# Silver Conventions

*Last reviewed: 2026-07-23 — provenance: verified external*

- Product: COMEX Silver futures (`SI`) and monthly Silver options (`SO`).
- Contract unit: 5,000 troy ounces; quotation: USD per troy ounce.
- SI outright futures minimum fluctuation is $0.005/oz; settlement and calendar
  spreads may use $0.001/oz. SO premium minimum fluctuation is $0.001/oz.
- SI futures last trade on the third-last business day of the delivery month.
- Since CME's April 2025 listing-cycle expansion, SI lists 26 consecutive
  monthly contracts plus July and December within the nearest 60 months.
- Monthly SO options are American-style and expire four business days before
  the end of the preceding month, subject to the Friday/pre-holiday adjustment.
- Serial mappings are: JAN/FEB/MAR options into MAR SI; APR/MAY into MAY;
  JUN/JUL into JUL; AUG/SEP into SEP; and OCT/NOV/DEC into DEC.
- Code source of truth: `ccvm/src/ccvm/reference/silver_calendar.py` and the
  `bulletin.underlying_month_map` in `ccvm/config/markets/silver.yaml`.
- Official sources: [COMEX Chapter 112](https://www.cmegroup.com/content/dam/cmegroup/rulebook/COMEX/1a/112.pdf),
  [COMEX Chapter 116](https://www.cmegroup.com/content/dam/cmegroup/rulebook/COMEX/1a/116.pdf),
  [CME Silver contract specifications](https://www.cmegroup.com/markets/metals/precious/silver.contractSpecs.html),
  [CME April 2025 listing-cycle notice](https://www.cmegroup.com/notices/electronic-trading/2025/03/20250331.html),
  and CME Section 64 Metals Option Products bulletins.

Settlement-only caveat: the system describes settled curves and option
surfaces; it does not establish current executability or confirmed mispricing.

# Gold Conventions

*Last reviewed: 2026-07-16 — provenance: verified external*

- Product: COMEX Gold futures (`GC`) and monthly Gold options (`OG`).
- Contract unit: 100 troy ounces; quotation: USD per troy ounce; minimum
  fluctuation: $0.10/oz ($10 per contract).
- GC futures last trade: third-last business day of the delivery month.
- OG monthly expiry: four business days before the end of the preceding month,
  with the Friday/pre-holiday adjustment in COMEX Rule 115101.E.
- Serial mapping is not a fixed offset: JAN/FEB options exercise into FEB GC,
  MAR/APR into APR, MAY/JUN into JUN, JUL/AUG into AUG, SEP/OCT into OCT, and
  NOV/DEC into DEC.
- Code source of truth: `ccvm/src/ccvm/reference/gold_calendar.py` and the
  `bulletin.underlying_month_map` in `ccvm/config/markets/gold.yaml`.
- Official sources: CME/COMEX Rulebook Chapters 113 and 115, CME Gold contract
  specifications, CME Section 64 Metals Option Products bulletin.

Settlement-only caveat: the system describes settled curves and option surfaces;
it does not establish current executability or confirmed mispricing.

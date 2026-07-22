# Corn Conventions

*Last reviewed: 2026-07-21 — provenance: verified external*

- Product: CBOT Corn futures (`ZC`) and standard/serial Corn options (Globex
  code `OZC`; daily bulletin headings `CORN CALL` and `CORN PUT`).
- Contract unit: 5,000 bushels. Futures are quoted in cents per bushel with a
  quarter-cent minimum fluctuation ($12.50 per contract). Standard option
  premiums trade in eighths of one cent ($6.25 per contract).
- CurveLens converts both futures and option premiums to USD per bushel before
  pricing, spread, and volatility calculations.
- Listed futures delivery months are March, May, July, September, and December.
  Serial option months exercise into the next listed futures month.
- Futures trading terminates on the business day before the 15th calendar day
  of the delivery month.
- Standard/serial options terminate on the last Friday preceding by at least
  two business days the last business day of the month before the named option
  month, with a prior-business-day adjustment when Friday is closed.
- Code source of truth: `ccvm/src/ccvm/reference/corn_calendar.py` and
  `ccvm/config/markets/corn.yaml`.
- Official sources: CBOT Rulebook Chapters 10 and 10A, CME Corn contract
  specifications, and CME Section 56 Corn/Oat/Rough Rice Options bulletin.

Settlement-only caveat: futures settlements are not cash bids, local basis,
freight, or executable spread quotes.

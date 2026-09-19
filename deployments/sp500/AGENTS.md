# AGENTS.md — CurveLens S&P 500 Deployment

Follow the repository `AGENTS.md` plus this runbook.

## Status

**Experimental — validation only.** Analyze quarterly CME `ES` futures and
matching quarterly options. Daily, weekly, serial, and end-of-month option
families, schedules, and delivery are excluded.

Always set:

```bash
export CCVM_PRODUCT=sp500
```

State is isolated under `ccvm/data/products/sp500/`.

## Inputs

- Place reviewed canonical `futures.json` and `options.json` in
  `authorized_market_data/trade_date=<date>/` under the product data directory.
- Set `FRED_API_KEY` for macro history.
- Set `ALPHAVANTAGE_API_KEY` for watched upcoming earnings.
- Set `SEC_USER_AGENT` to a descriptive contact identity for EDGAR access; it
  is configuration, not a secret key.
- SPY and sector ETFs are approximate context only.
- Use `knowledge/sp500/` for interpretation.

Invoke: **Use `$curvelens-daily-analysis` to run S&P 500 for `<date>`.**
Resume durable state without `--restart` unless explicitly directed.

## Acceptance gates

Validate authorized ES settlements, quarterly contract/expiry identity,
exercise style, premium units and ticks, dividend/rate basis interpretation,
all in-horizon IV/RND surfaces, SPY timing, sector proxy labels, SEC identity
and rate policy, and earnings-calendar freshness over consecutive dates.

Never fabricate data, treat ETF context as a CME settlement, represent sector
ETFs as exact attribution, call a model API from repository code, schedule,
prepare delivery, or send without explicit approval.

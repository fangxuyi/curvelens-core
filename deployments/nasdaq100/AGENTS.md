# AGENTS.md — CurveLens Nasdaq-100 Deployment

Follow the repository `AGENTS.md` plus this runbook.

## Status

**Experimental — validation only.** Analyze quarterly CME `NQ` futures and
matching quarterly options. Other option families, schedules, and delivery are
excluded.

```bash
export CCVM_PRODUCT=nasdaq100
```

State is isolated under `ccvm/data/products/nasdaq100/`.

Provide reviewed canonical exchange `futures.json` and `options.json` under
`authorized_market_data/trade_date=<date>/`. Set `FRED_API_KEY`,
`ALPHAVANTAGE_API_KEY`, and a descriptive `SEC_USER_AGENT`. QQQ, sector ETFs,
and the company watchlist are bounded context rather than authoritative
settlement or complete membership data. Interpret with `knowledge/nasdaq100/`.

Invoke: **Use `$curvelens-daily-analysis` to run Nasdaq-100 for `<date>`.**

Before enablement, validate NQ contract and quarterly-option identity, expiry
and exercise conventions, units/ticks, IV/RND surfaces, QQQ timing, sector
labels, SEC access, earnings freshness, and specialist evidence over several
dates. Never fabricate inputs, call a model API from repo code, enable a
schedule, prepare delivery, or send without explicit approval.

# AGENTS.md — CurveLens Russell 2000 Deployment

Follow the repository `AGENTS.md` plus this runbook.

## Status

**Experimental — validation only.** Analyze quarterly CME `RTY` futures and
matching quarterly options. Other option families, schedules, and delivery are
excluded.

```bash
export CCVM_PRODUCT=russell2000
```

State is isolated under `ccvm/data/products/russell2000/`.

Provide reviewed canonical exchange `futures.json` and `options.json` under
`authorized_market_data/trade_date=<date>/`. Set `FRED_API_KEY`,
`ALPHAVANTAGE_API_KEY`, and a descriptive `SEC_USER_AGENT`. IWM, broad sector
ETFs, and the company watchlist are context—not exact Russell attribution or
authoritative membership. Interpret with `knowledge/russell2000/`.

Invoke: **Use `$curvelens-daily-analysis` to run Russell 2000 for `<date>`.**

Before enablement validate RTY quarterly identities, expiry/exercise
conventions, units/ticks, IV/RND surfaces, IWM timing, proxy labeling, credit
series cadence, SEC access, earnings freshness, and specialist evidence over
several dates. Never fabricate inputs, call a model API from repo code, enable
a schedule, prepare delivery, or send without explicit approval.

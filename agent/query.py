#!/usr/bin/env python
"""Read-only query toolkit for on-demand Q&A (D5).

When a user asks the agent a question ("what happened to M1-M2 this month?",
"how has skew moved since the Hormuz scare?"), the agent answers from the
gold layer through THIS toolkit instead of hand-rolling pandas — every answer
stays reproducible and citable. stdout is always one JSON document.

Commands:
    series  --metric <m> [--days N]     headline metric time series
                                        (from gold/history_context)
    curve   [--date D]                  futures curve for a date
    surface [--date D]                  per-expiry vol surface summary
    state                               scenario state + streaks + scorecard
    detail  --kind oi|cot|rnd|triggers|eia_seasonal [--date D]
                                        the stored JSON artifacts
    dates                               available gold trade dates
    sql     --query "SELECT ..."        DuckDB over the gold parquet layer
                                        (SELECT-only; mutating keywords blocked)

All access is read-only. `--date` defaults to the latest gold date.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CCVM_DIR = REPO_ROOT / "ccvm"
sys.path.insert(0, str(CCVM_DIR / "src"))
from ccvm.runtime import data_dir

DATA_DIR = data_dir()

SERIES_METRICS = [
    "front_settle", "curve_slope", "m1_m2_spread", "atm_iv", "rr25", "bf25",
    "skew_slope", "realized_vol_10d", "vrp_10d", "benchmark_spread",
]
SERIES_ALIASES = {"brent_wti_spread": "benchmark_spread"}

_SQL_BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|create|alter|copy|export|attach|install|load|pragma|set)\b",
    re.IGNORECASE,
)


def _emit(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


def _pq():
    from ccvm.storage.parquet_store import ParquetStore
    return ParquetStore(DATA_DIR)


def _latest_date(pq) -> str | None:
    dates = pq.list_dates("gold", "futures_features")
    return dates[-1] if dates else None


def cmd_dates(_args) -> None:
    _emit({"gold_dates": _pq().list_dates("gold", "futures_features")})


def cmd_series(args) -> None:
    pq = _pq()
    if args.metric not in SERIES_METRICS and args.metric not in SERIES_ALIASES:
        _emit({"error": f"unknown metric {args.metric!r}",
               "available": SERIES_METRICS + sorted(SERIES_ALIASES)})
        return
    metric = SERIES_ALIASES.get(args.metric, args.metric)
    dates = pq.list_dates("gold", "history_context")[-args.days:]
    rows = []
    for dt in dates:
        d = pq.read("gold", "history_context", dt).to_pydict()
        if metric in d:
            rows.append({"date": dt, "value": d[metric][0]})
        elif metric == "benchmark_spread" and "brent_wti_spread" in d:
            rows.append({"date": dt, "value": d["brent_wti_spread"][0]})
    _emit({"metric": args.metric, "rows": rows,
           "source": "gold/history_context", "unit_note": "IV/RR/vol as decimals"})


def cmd_curve(args) -> None:
    pq = _pq()
    dt = args.date or _latest_date(pq)
    if not dt or not pq.exists("gold", "futures_features", dt):
        _emit({"error": f"no gold futures for {dt}"})
        return
    d = pq.read("gold", "futures_features", dt).to_pydict()
    _emit({"date": dt, "source": "gold/futures_features",
           "contracts": [
               {"code": d["contract_code"][i], "delivery": d["delivery_month"][i],
                "settle": d["settlement"][i], "spread_to_next": d["spread_to_next"][i]}
               for i in range(len(d["contract_code"]))
           ]})


def cmd_surface(args) -> None:
    pq = _pq()
    dt = args.date or _latest_date(pq)
    if not dt or not pq.exists("gold", "option_features", dt):
        _emit({"error": f"no gold options for {dt}"})
        return
    d = pq.read("gold", "option_features", dt).to_pydict()
    seen: dict[str, dict] = {}
    for i in range(len(d["option_expiry"])):
        exp = d["option_expiry"][i]
        if exp not in seen and d["atm_iv"][i] is not None:
            seen[exp] = {"expiry": exp, "atm_iv": d["atm_iv"][i],
                         "rr25": d["risk_reversal_25d"][i],
                         "bf25": d["butterfly_25d"][i],
                         "skew_slope": d["skew_slope"][i],
                         "forward": d["forward_price"][i]}
    _emit({"date": dt, "source": "gold/option_features",
           "expiries": [seen[e] for e in sorted(seen)]})


def cmd_state(_args) -> None:
    out = {}
    for name, rel in (("scenario_state", "state/scenario_state.json"),
                      ("scorecard", "state/scorecard.json")):
        p = DATA_DIR / rel
        out[name] = json.loads(p.read_text()) if p.exists() else None
    from ccvm.analytics import monitor_state
    pq = _pq()
    latest = _latest_date(pq)
    if latest:
        out["streaks"] = monitor_state.compute_streaks(pq, DATA_DIR, latest)
        out["as_of"] = latest
    _emit(out)


def cmd_detail(args) -> None:
    pq = _pq()
    dt = args.date or _latest_date(pq)
    kinds = {"oi": "oi/trade_date={d}/oi.json",
             "cot": "cot/trade_date={d}/cot.json",
             "rnd": "rnd/trade_date={d}/rnd.json",
             "triggers": "triggers/trade_date={d}/triggers.json",
             "eia_seasonal": "eia_seasonal/trade_date={d}/seasonal.json"}
    if args.kind not in kinds:
        _emit({"error": f"unknown kind {args.kind!r}", "available": sorted(kinds)})
        return
    p = DATA_DIR / "gold" / kinds[args.kind].format(d=dt)
    _emit(json.loads(p.read_text()) if p.exists()
          else {"error": f"no {args.kind} for {dt}"})


def cmd_sql(args) -> None:
    q = args.query.strip().rstrip(";")
    if not q.lower().startswith("select") or _SQL_BLOCKED.search(q):
        _emit({"error": "SELECT-only: mutating/side-effect keywords are blocked"})
        return
    import duckdb
    con = duckdb.connect(":memory:")
    # expose each gold parquet dataset as a view over its date partitions
    for ds in ("futures_features", "option_features", "eia_features", "history_context"):
        glob = str(DATA_DIR / "gold" / ds / "trade_date=*" / "data.parquet")
        try:
            con.execute(
                f"CREATE VIEW {ds} AS SELECT * FROM read_parquet('{glob}')")
        except duckdb.Error:
            pass  # dataset may not exist yet
    try:
        res = con.execute(q)
        cols = [c[0] for c in res.description]
        rows = res.fetchmany(500)
        _emit({"columns": cols, "rows": [list(r) for r in rows],
               "row_limit": 500, "views": ["futures_features", "option_features",
                                           "eia_features", "history_context"]})
    except duckdb.Error as exc:
        _emit({"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="CurveLens read-only Q&A toolkit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dates")
    p = sub.add_parser("series")
    p.add_argument("--metric", required=True)
    p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("curve")
    p.add_argument("--date")
    p = sub.add_parser("surface")
    p.add_argument("--date")
    sub.add_parser("state")
    p = sub.add_parser("detail")
    p.add_argument("--kind", required=True)
    p.add_argument("--date")
    p = sub.add_parser("sql")
    p.add_argument("--query", required=True)
    args = parser.parse_args()

    {"dates": cmd_dates, "series": cmd_series, "curve": cmd_curve,
     "surface": cmd_surface, "state": cmd_state, "detail": cmd_detail,
     "sql": cmd_sql}[args.cmd](args)


if __name__ == "__main__":
    main()

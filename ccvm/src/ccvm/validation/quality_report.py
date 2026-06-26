"""
Daily quality report generator.

Reads silver Parquet tables, runs quality checks, and writes a JSON report to
data/quality_reports/YYYY-MM-DD.json plus a human-readable Markdown summary.

Report structure:
  {
    "trade_date": "YYYY-MM-DD",
    "generated_at": "...",
    "overall_status": "PASS|WARN|FAIL|INSUFFICIENT_DATA",
    "futures": { ... },
    "options": { ... },
    "fundamentals": { ... },
    "caveats": [...]
  }
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.compute as pc


def _count_by_status(table: pa.Table, col: str = "silver_status") -> dict[str, int]:
    if col not in table.schema.names:
        return {}
    vals = table.column(col).to_pylist()
    counts: dict[str, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    return counts


def _col_values(table: pa.Table, col: str) -> list:
    if col not in table.schema.names:
        return []
    return [v for v in table.column(col).to_pylist() if v is not None]


def futures_section(silver_futures: Optional[pa.Table]) -> dict:
    if silver_futures is None or len(silver_futures) == 0:
        return {"status": "INSUFFICIENT_DATA", "record_count": 0, "notes": ["no silver futures data"]}

    n = len(silver_futures)
    by_status = _count_by_status(silver_futures)
    pass_n = by_status.get("PASS", 0)
    warn_n = by_status.get("WARN", 0)
    fail_n = by_status.get("FAIL", 0)

    contracts = _col_values(silver_futures, "contract_code")
    settlements = _col_values(silver_futures, "settlement")
    delivery_months = sorted(set(_col_values(silver_futures, "delivery_month")))
    curve_positions = _col_values(silver_futures, "curve_position")

    notes = []
    status = "PASS"

    if fail_n > 0:
        status = "FAIL"
        notes.append(f"{fail_n} rows failed quality checks")
    if warn_n > 0:
        status = max(status, "WARN") if status == "PASS" else status
        notes.append(f"{warn_n} rows have warnings")

    front_month = delivery_months[0] if delivery_months else None
    back_month = delivery_months[-1] if delivery_months else None

    # Check for gaps in curve
    if len(delivery_months) > 1:
        prev_ym = None
        gaps = []
        for ym in delivery_months:
            if prev_ym is not None:
                y0, m0 = int(prev_ym[:4]), int(prev_ym[5:])
                y1, m1 = int(ym[:4]), int(ym[5:])
                expected_next = (y0, m0 % 12 + 1) if m0 < 12 else (y0 + 1, 1)
                if (y1, m1) != expected_next:
                    gaps.append(f"gap between {prev_ym} and {ym}")
            prev_ym = ym
        if gaps:
            notes.extend(gaps[:3])

    return {
        "status": status,
        "record_count": n,
        "pass_count": pass_n,
        "warn_count": warn_n,
        "fail_count": fail_n,
        "contract_count": len(set(contracts)),
        "front_month": front_month,
        "back_month": back_month,
        "settlement_range": [min(settlements), max(settlements)] if settlements else None,
        "notes": notes,
    }


def options_section(silver_options: Optional[pa.Table]) -> dict:
    if silver_options is None or len(silver_options) == 0:
        return {"status": "INSUFFICIENT_DATA", "record_count": 0, "notes": ["no silver options data"]}

    n = len(silver_options)
    by_status = _count_by_status(silver_options)
    pass_n = by_status.get("PASS", 0)
    warn_n = by_status.get("WARN", 0)
    fail_n = by_status.get("FAIL", 0)

    expiries = sorted(set(_col_values(silver_options, "option_expiry")))
    underlyings = sorted(set(_col_values(silver_options, "underlying_contract")))
    ivs = [v for v in _col_values(silver_options, "implied_volatility") if v is not None and v > 0]

    notes = []
    if fail_n > 0:
        notes.append(f"{fail_n} rows failed quality checks")
    if warn_n > 0:
        notes.append(f"{warn_n} rows have coverage warnings")

    # Per-expiry strike counts
    expiry_coverage: dict[str, dict] = {}
    if "option_expiry" in silver_options.schema.names:
        d = silver_options.to_pydict()
        from collections import defaultdict
        strikes_by_exp_side: dict[tuple, set] = defaultdict(set)
        for i in range(n):
            k = (d["option_expiry"][i], d["call_put"][i])
            s = d["strike"][i]
            if s is not None and s > 0:
                strikes_by_exp_side[k].add(s)
        for (exp, cp), strikes in strikes_by_exp_side.items():
            if exp not in expiry_coverage:
                expiry_coverage[exp] = {}
            expiry_coverage[exp][cp] = len(strikes)

    status = "FAIL" if fail_n > n // 2 else ("WARN" if (warn_n > 0 or fail_n > 0) else "PASS")

    return {
        "status": status,
        "record_count": n,
        "pass_count": pass_n,
        "warn_count": warn_n,
        "fail_count": fail_n,
        "expiry_count": len(expiries),
        "expiries": expiries[:10],
        "underlyings": underlyings,
        "atm_iv_range": [min(ivs), max(ivs)] if ivs else None,
        "expiry_strike_coverage": expiry_coverage,
        "notes": notes,
    }


def fundamentals_section(silver_eia: Optional[pa.Table]) -> dict:
    if silver_eia is None or len(silver_eia) == 0:
        return {"status": "INSUFFICIENT_DATA", "record_count": 0, "notes": ["no EIA data"]}

    n = len(silver_eia)
    periods = sorted(set(_col_values(silver_eia, "period")))
    values = _col_values(silver_eia, "value")
    series = sorted(set(_col_values(silver_eia, "series_id")))

    return {
        "status": "PASS",
        "record_count": n,
        "latest_period": periods[-1] if periods else None,
        "series": series,
        "value_range": [min(values), max(values)] if values else None,
        "notes": [],
    }


_DELTA_WARN_THRESHOLD = 0.05
_DELTA_FAIL_THRESHOLD = 0.10


def delta_check_section(gold_options: pa.Table) -> dict:
    """
    Compare market delta (CME bulletin, per-strike) against Black-76 model delta.

    Discrepancies flag: model miscalibration, American-style early-exercise
    premium (expected for deep ITM puts), or bulletin data quality issues.
    Both deltas should be signed (calls positive, puts negative).
    """
    if gold_options is None or len(gold_options) == 0:
        return {"status": "INSUFFICIENT_DATA", "record_count": 0, "compared": 0,
                "notes": ["no gold options data"]}

    d = gold_options.to_pydict()
    n = len(d["trade_date"])
    market_deltas = d.get("market_delta", [None] * n)
    model_deltas = d.get("black76_delta", [None] * n)

    diffs: list[float] = []
    for i in range(n):
        md = market_deltas[i]
        bd = model_deltas[i]
        if md is None or bd is None or md != md or bd != bd:  # skip None / NaN
            continue
        diffs.append(abs(md - bd))

    if not diffs:
        return {
            "status": "INSUFFICIENT_DATA",
            "record_count": n,
            "compared": 0,
            "notes": ["no rows with both market_delta and black76_delta populated"],
        }

    mean_diff = sum(diffs) / len(diffs)
    max_diff = max(diffs)
    pct_large = sum(1 for x in diffs if x > _DELTA_WARN_THRESHOLD) / len(diffs)

    if mean_diff >= _DELTA_FAIL_THRESHOLD:
        status = "FAIL"
    elif mean_diff >= _DELTA_WARN_THRESHOLD or pct_large > 0.20:
        status = "WARN"
    else:
        status = "PASS"

    notes: list[str] = []
    if pct_large > 0.10:
        notes.append(
            f"{pct_large:.0%} of options have |market_delta − model_delta| > "
            f"{_DELTA_WARN_THRESHOLD} — likely American-style premium on deep ITM puts"
        )

    return {
        "status": status,
        "record_count": n,
        "compared": len(diffs),
        "mean_abs_delta_diff": round(mean_diff, 4),
        "max_abs_delta_diff": round(max_diff, 4),
        "pct_diff_over_0_05": round(pct_large, 4),
        "thresholds": {"warn": _DELTA_WARN_THRESHOLD, "fail": _DELTA_FAIL_THRESHOLD},
        "notes": notes,
    }


def generate(
    trade_date: date,
    silver_futures: Optional[pa.Table],
    silver_options: Optional[pa.Table],
    silver_eia: Optional[pa.Table],
    output_dir: Path,
    caveats: list[str] | None = None,
) -> dict:
    fut = futures_section(silver_futures)
    opt = options_section(silver_options)
    fund = fundamentals_section(silver_eia)

    statuses = [s["status"] for s in [fut, opt, fund]]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARN" in statuses:
        overall = "WARN"
    elif all(s == "INSUFFICIENT_DATA" for s in statuses):
        overall = "INSUFFICIENT_DATA"
    else:
        overall = "PASS"

    report = {
        "trade_date": trade_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "futures": fut,
        "options": opt,
        "fundamentals": fund,
        "caveats": (caveats or []) + [
            "settlement_data_only_not_executable_mispricing",
            "LO_options_American_style_black76_IV_is_European_approximation",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{trade_date.isoformat()}.json"
    json_path.write_text(json.dumps(report, indent=2))

    # Markdown summary
    md_path = output_dir / f"{trade_date.isoformat()}.md"
    md_path.write_text(_render_markdown(report))

    return report


def _render_markdown(report: dict) -> str:
    td = report["trade_date"]
    overall = report["overall_status"]
    fut = report["futures"]
    opt = report["options"]
    fund = report["fundamentals"]

    lines = [
        f"# CCVM Quality Report — {td}",
        f"\n**Overall status:** {overall}  |  Generated: {report['generated_at']}\n",
        "## Futures",
        f"- Status: **{fut['status']}**",
        f"- Contracts: {fut.get('contract_count', 0)}  (PASS={fut.get('pass_count', 0)}, WARN={fut.get('warn_count', 0)}, FAIL={fut.get('fail_count', 0)})",
        f"- Curve: {fut.get('front_month', '?')} → {fut.get('back_month', '?')}",
    ]
    if fut.get("settlement_range"):
        lo, hi = fut["settlement_range"]
        lines.append(f"- Settlement range: ${lo:.2f} – ${hi:.2f}/bbl")
    for note in fut.get("notes", []):
        lines.append(f"- ⚠ {note}")

    lines += [
        "\n## Options",
        f"- Status: **{opt['status']}**",
        f"- Records: {opt.get('record_count', 0)}  Expiries: {opt.get('expiry_count', 0)}",
        f"- Underlyings: {opt.get('underlyings', [])}",
    ]
    if opt.get("atm_iv_range"):
        lo, hi = opt["atm_iv_range"]
        lines.append(f"- IV range: {lo:.1%} – {hi:.1%}")
    for note in opt.get("notes", []):
        lines.append(f"- ⚠ {note}")

    lines += [
        "\n## EIA Fundamentals",
        f"- Status: **{fund['status']}**",
        f"- Latest period: {fund.get('latest_period', 'N/A')}",
        f"- Series: {fund.get('series', [])}",
    ]

    dc = report.get("delta_check")
    if dc:
        lines += [
            "\n## Delta Check (market vs Black-76)",
            f"- Status: **{dc['status']}**",
            f"- Compared: {dc.get('compared', 0)} of {dc.get('record_count', 0)} rows",
            f"- Mean |Δ|: {dc.get('mean_abs_delta_diff', 'N/A')}  Max |Δ|: {dc.get('max_abs_delta_diff', 'N/A')}",
            f"- Fraction with |Δ| > 0.05: {dc.get('pct_diff_over_0_05', 0):.1%}",
        ]
        for note in dc.get("notes", []):
            lines.append(f"- ⚠ {note}")

    lines += ["\n## Caveats"]
    for c in report.get("caveats", []):
        lines.append(f"- {c}")

    return "\n".join(lines) + "\n"

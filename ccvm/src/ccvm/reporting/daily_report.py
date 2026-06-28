"""
Daily forward-risk brief generator.

Produces a structured Markdown report saved to data/reports/YYYY-MM-DD.md
and a JSON version at data/reports/YYYY-MM-DD.json.

Sections (per spec):
  1. Current market-implied risk
  2. Upcoming catalysts (top 5 ranked)
  3. Futures-options agreement
  4. Scenarios: bull / base / bear
  5. Confirmation and invalidation triggers
  6. Data caveats
  7. Next review

Every numerical statement references its feature source.
Every event claim references its source_url and event_id.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa


def generate(
    trade_date: date,
    gold_futures: Optional[pa.Table],
    gold_options: Optional[pa.Table],
    scenarios: list[dict],
    agreement: dict,
    top_catalysts: list[dict],
    quality_report: dict,
    output_dir: Path,
    gold_eia: Optional[pa.Table] = None,
) -> dict:
    """
    Generate the daily report. Returns the report dict and writes files to output_dir.
    """
    report = {
        "trade_date": trade_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {
            "market_risk": _market_risk_section(gold_futures, gold_options),
            "eia_fundamentals": _eia_section(gold_eia),
            "catalysts": _catalysts_section(top_catalysts),
            "agreement": agreement,
            "scenarios": scenarios,
            "data_caveats": _caveats(quality_report),
            "next_review": _next_review(trade_date, top_catalysts),
        },
        "quality_status": quality_report.get("overall_status", "UNKNOWN"),
        "source_feature_ids": {
            "gold_futures": f"gold/futures_features/trade_date={trade_date.isoformat()}",
            "gold_options": f"gold/option_features/trade_date={trade_date.isoformat()}",
            "agreement": f"gold/agreement/trade_date={trade_date.isoformat()}",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{trade_date.isoformat()}.json"
    json_path.write_text(json.dumps(report, indent=2))

    md_path = output_dir / f"{trade_date.isoformat()}.md"
    md_path.write_text(_render_markdown(report))

    return report


def _market_risk_section(
    gold_futures: Optional[pa.Table],
    gold_options: Optional[pa.Table],
) -> dict:
    section: dict = {"status": "insufficient_data"}

    if gold_futures is not None and len(gold_futures) > 0:
        fd = gold_futures.to_pydict()
        n = len(fd["trade_date"])
        section["futures"] = {
            "front_contract": fd["contract_code"][0] if n > 0 else None,
            "front_settlement": fd["settlement"][0] if n > 0 else None,
            "curve_slope_per_month": fd["front_back_slope"][0] if n > 0 else None,
            "contango": fd["contango_flag"][0] if n > 0 else None,
            "active_contracts": n,
            "feature_source": "gold/futures_features",
        }
        section["status"] = "available"

    if gold_options is not None and len(gold_options) > 0:
        od = gold_options.to_pydict()
        expiries = sorted(set(od["option_expiry"]))
        atm_ivs = [v for v in od["atm_iv"] if v is not None]
        rr25_vals = [v for v in od["risk_reversal_25d"] if v is not None]
        section["options"] = {
            "expiries_covered": expiries[:6],
            "atm_iv": atm_ivs[0] if atm_ivs else None,
            "risk_reversal_25d": rr25_vals[0] if rr25_vals else None,
            "coverage_note": od["price_note"][0] if od.get("price_note") else None,
            "feature_source": "gold/option_features",
        }

    return section


def _eia_section(gold_eia: Optional[pa.Table]) -> dict:
    if gold_eia is None or len(gold_eia) == 0:
        return {"status": "unavailable"}
    d = gold_eia.to_pydict()
    return {
        "status": "available",
        "eia_period": d["eia_period"][0],
        "crude_stocks_ex_spr_mbbl": d["crude_stocks_ex_spr"][0],
        "cushing_stocks_mbbl": d["cushing_stocks"][0],
        "crude_draw_mbbl": d["crude_draw"][0],
        "cushing_draw_mbbl": d["cushing_draw"][0],
        "crude_imports_mbbld": d["crude_imports"][0],
        "crude_exports_mbbld": d["crude_exports"][0],
        "net_imports_mbbld": d["net_imports"][0],
        "refinery_utilization_pct": d["refinery_utilization_pct"][0],
        "gasoline_stocks_mbbl": d["gasoline_stocks"][0],
        "distillate_stocks_mbbl": d["distillate_stocks"][0],
        "gasoline_draw_mbbl": d["gasoline_draw"][0],
        "distillate_draw_mbbl": d["distillate_draw"][0],
        "supply_signal": d["supply_signal"][0],
        "cushing_signal": d["cushing_signal"][0],
        "scenario_trigger": d["scenario_trigger"][0],
    }


def _catalysts_section(top_catalysts: list[dict]) -> dict:
    return {
        "count": len(top_catalysts),
        "top_events": [
            {
                "rank": e.get("relevance_rank"),
                "score": e.get("relevance_score"),
                "event_id": e.get("event_id"),
                "title": e.get("title"),
                "direction": e.get("direction"),
                "magnitude": e.get("magnitude"),
                "affected_horizon": e.get("affected_horizon"),
                "effective_start": e.get("effective_start"),
                "source_url": e.get("source_url"),
            }
            for e in top_catalysts[:5]
        ],
    }


def _caveats(quality_report: dict) -> list[str]:
    caveats = [
        "settlement_data_only: prices are EOD settlements, not executable quotes",
        "USO_option_proxy: options data is from USO equity options, not CL futures options",
        "black76_approximation: IV computed under European option assumption; WTI options are American",
    ]
    qr_caveats = quality_report.get("caveats", [])
    caveats.extend(c for c in qr_caveats if c not in caveats)
    return caveats


def _next_review(trade_date: date, top_catalysts: list[dict]) -> dict:
    # Find nearest catalyst start date
    upcoming_dates = []
    for e in top_catalysts:
        start = e.get("effective_start")
        if start:
            try:
                d = date.fromisoformat(start)
                if d >= trade_date:
                    upcoming_dates.append((d, e.get("title", "")))
            except ValueError:
                pass

    upcoming_dates.sort()
    next_event = upcoming_dates[0] if upcoming_dates else None

    return {
        "next_eia_release": "Next Wednesday 10:30 ET",
        "next_catalyst_date": next_event[0].isoformat() if next_event else None,
        "next_catalyst_title": next_event[1] if next_event else None,
    }


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    return f"{v:.1%}"


def _fmt_usd(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    return f"${v:.2f}"


def _render_markdown(report: dict) -> str:
    td = report["trade_date"]
    gen = report["generated_at"]
    s = report["sections"]
    quality = report["quality_status"]

    fut = s["market_risk"].get("futures", {})
    opt = s["market_risk"].get("options", {})
    eia = s.get("eia_fundamentals", {})
    agr = s["agreement"]
    cats = s["catalysts"]
    scenarios = s["scenarios"]
    caveats = s["data_caveats"]
    next_rev = s["next_review"]

    lines = [
        f"# CCVM Daily Forward-Risk Brief — {td}",
        f"\n*Generated: {gen}  |  Data quality: **{quality}***",
        "",
    ]

    # ── Section 1: Market Risk ──
    lines += ["## 1. Current Market-Implied Risk", ""]
    if fut:
        slope = fut.get("curve_slope_per_month")
        slope_str = f"${slope:+.3f}/month" if slope is not None else "N/A"
        structure = "CONTANGO" if fut.get("contango") else "BACKWARDATION"
        lines += [
            f"**WTI Futures** (source: `{fut.get('feature_source')}`)",
            f"- Front contract: **{fut.get('front_contract')}** @ **{_fmt_usd(fut.get('front_settlement'))}/bbl**",
            f"- Curve structure: **{structure}** (slope: {slope_str})",
            f"- Active contracts: {fut.get('active_contracts')}",
            "",
        ]
    if opt:
        lines += [
            f"**Options Surface** (source: `{opt.get('feature_source')}`)",
            f"- ATM IV: **{_fmt_pct(opt.get('atm_iv'))}**",
            f"- 25Δ Risk Reversal: **{_fmt_pct(opt.get('risk_reversal_25d'))}**",
            f"- ⚠ Note: *{opt.get('coverage_note')}*",
            "",
        ]

    # ── Section 2: EIA Fundamentals ──
    lines += ["## 2. EIA Weekly Fundamentals", ""]
    if eia.get("status") == "available":
        draw = eia.get("crude_draw_mbbl")
        cush_draw = eia.get("cushing_draw_mbbl")
        util = eia.get("refinery_utilization_pct")
        signal = eia.get("supply_signal", "neutral").upper()
        trigger = eia.get("scenario_trigger", "none")
        draw_str = f"{draw:+,.0f} MBBL" if draw is not None else "N/A"
        cush_str = f"{cush_draw:+,.0f} MBBL" if cush_draw is not None else "N/A"
        util_str = f"{util:.1f}%" if util is not None else "N/A"
        net_imp = eia.get("net_imports_mbbld")
        net_str = f"{net_imp:+.0f} MBBL/D" if net_imp is not None else "N/A"
        def _mbbl(v):
            return f"{v:,.0f} MBBL" if v is not None else "N/A"
        def _mbbld_wow(v):
            return f"{v:+,.0f} MBBL" if v is not None else "N/A"
        crude_val  = _mbbl(eia.get("crude_stocks_ex_spr_mbbl"))
        cush_val   = _mbbl(eia.get("cushing_stocks_mbbl"))
        gas_val    = _mbbl(eia.get("gasoline_stocks_mbbl"))
        dist_val   = _mbbl(eia.get("distillate_stocks_mbbl"))
        gas_wow    = _mbbld_wow(eia.get("gasoline_draw_mbbl"))
        dist_wow   = _mbbld_wow(eia.get("distillate_draw_mbbl"))
        lines += [
            f"*Week ending {eia.get('eia_period', 'N/A')}*",
            "",
            "| Metric | Value | WoW |",
            "|--------|-------|-----|",
            f"| U.S. crude stocks (ex-SPR) | {crude_val} | **{draw_str}** |",
            f"| Cushing stocks | {cush_val} | {cush_str} |",
            f"| Refinery utilization | {util_str} | — |",
            f"| Net imports | {net_str} | — |",
            f"| Gasoline stocks | {gas_val} | {gas_wow} |",
            f"| Distillate stocks | {dist_val} | {dist_wow} |",
            "",
            f"**Supply signal:** `{signal}`  |  **Scenario trigger:** `{trigger}`",
            "",
        ]
    else:
        lines.append("*EIA data not available for this date.*\n")

    # ── Section 3: Catalysts ──
    lines += ["## 3. Upcoming Catalysts", ""]
    if cats["count"] == 0:
        lines.append("*No catalyst events on record for this date.*\n")
    else:
        lines.append(f"*{cats['count']} event(s) extracted and ranked.*\n")
        lines.append("| # | Score | Direction | Title | Horizon |")
        lines.append("|---|-------|-----------|-------|---------|")
        for ev in cats["top_events"]:
            title = (ev.get("title") or "")[:60]
            lines.append(
                f"| {ev.get('rank')} | {ev.get('score')} | "
                f"{ev.get('direction')} | {title} | {ev.get('affected_horizon')} |"
            )
        lines.append("")

    # ── Section 3: Agreement ──
    lines += ["## 4. Futures-Options Agreement", ""]
    agr_state = agr.get("state", "insufficient_data")
    agr_conf = agr.get("confidence", "low")
    lines += [
        f"**State:** `{agr_state}`  |  **Confidence:** {agr_conf}",
        "",
        "**Evidence:**",
    ]
    for ev in agr.get("evidence", []):
        lines.append(f"- {ev}")
    lines.append("")

    # ── Section 4: Scenarios ──
    lines += ["## 5. Scenarios", ""]
    for sc in scenarios:
        name = sc.get("name", "").upper()
        desc = sc.get("description", "")
        fi = sc.get("front_month_impact", 0)
        lines += [
            f"### {name}: {desc}",
            f"- **Front-month impact:** {fi:+.2f} $/bbl",
            f"- **Curve P&L estimate:** {sc.get('curve_pnl_estimate', 0):+.2f} $/bbl × contract count",
        ]
        shocked = sc.get("shocked_settlements", [])
        if shocked:
            front = shocked[0]
            back = shocked[-1]
            lines.append(
                f"- Shocked curve: {front['contract_code']} "
                f"{_fmt_usd(front['shocked_settlement'])} ... "
                f"{back['contract_code']} {_fmt_usd(back['shocked_settlement'])}"
            )
        vol_shifts = sc.get("vol_shifts", [])
        if vol_shifts:
            front_vol = vol_shifts[0]
            lines.append(
                f"- Front-expiry IV: {_fmt_pct(front_vol.get('base_atm_iv'))} → "
                f"{_fmt_pct(front_vol.get('shocked_iv'))} "
                f"({front_vol.get('diff_pp', 0):+.1f}pp)"
            )
        lines.append("")

    # ── Section 5: Triggers ──
    lines += ["## 6. Confirmation / Invalidation Triggers", ""]
    for sc in scenarios[:3]:  # bull/base/bear
        name = sc.get("name", "").upper()
        lines.append(f"**{name}**")
        for t in sc.get("confirmation_triggers", []):
            lines.append(f"- ✅ Confirms: {t}")
        for t in sc.get("invalidation_triggers", []):
            lines.append(f"- ❌ Invalidates: {t}")
        lines.append("")

    # ── Section 6: Caveats ──
    lines += ["## 7. Data Caveats", ""]
    for c in caveats:
        lines.append(f"- {c}")
    lines.append("")

    # ── Section 7: Next Review ──
    lines += ["## 8. Next Review", ""]
    lines += [
        f"- **EIA release:** {next_rev.get('next_eia_release', 'N/A')}",
        f"- **Next catalyst date:** {next_rev.get('next_catalyst_date', 'N/A')} — {next_rev.get('next_catalyst_title', '')}",
        "",
        "---",
        "*CCVM — Commodity Catalyst and Volatility Monitor. "
        "Settlement data only; does not establish executability or confirmed mispricing.*",
    ]

    return "\n".join(lines) + "\n"

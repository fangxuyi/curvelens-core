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
    history_context: Optional[pa.Table] = None,
    monitor: Optional[dict] = None,
    streaks: Optional[dict] = None,
    day_diff: Optional[dict] = None,
    oi: Optional[dict] = None,
    cot: Optional[dict] = None,
    eia_seasonal: Optional[dict] = None,
    rnd: Optional[dict] = None,
    themes: Optional[list] = None,
    scorecard: Optional[dict] = None,
) -> dict:
    """
    Generate the daily report. Returns the report dict and writes files to output_dir.
    """
    report = {
        "trade_date": trade_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {
            "what_changed": {"diff": day_diff or {"status": "unavailable"},
                             "streaks": streaks or {}},
            "market_risk": _market_risk_section(gold_futures, gold_options),
            "history_context": _history_context_section(history_context),
            "monitor": monitor or {},
            "oi": oi or {},
            "cot": cot or {},
            "eia_seasonal": eia_seasonal or {},
            "rnd": rnd or {},
            "term_structure": _term_structure_section(gold_futures, gold_options),
            "eia_fundamentals": _eia_section(gold_eia),
            "catalysts": _catalysts_section(top_catalysts, themes),
            "agreement": agreement,
            "scenarios": scenarios,
            "scorecard": scorecard or {},
            "data_caveats": _caveats(quality_report),
            "next_review": _next_review(trade_date, top_catalysts),
        },
        "quality_status": quality_report.get("overall_status", "UNKNOWN"),
        "quality_notes": _quality_notes(quality_report),
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


def _product_display() -> str:
    """Product display name from the profile (E1) — 'WTI', 'Henry Hub', ..."""
    try:
        from ..reference.product import get_product
        return get_product().display_name
    except Exception:
        return "WTI"


def _market_risk_section(
    gold_futures: Optional[pa.Table],
    gold_options: Optional[pa.Table],
) -> dict:
    section: dict = {"status": "insufficient_data"}

    if gold_futures is not None and len(gold_futures) > 0:
        fd = gold_futures.to_pydict()
        n = len(fd["trade_date"])
        front_spread = fd["spread_to_next"][0] if n > 0 else None  # M1-M2 spread
        section["futures"] = {
            "front_contract": fd["contract_code"][0] if n > 0 else None,
            "front_settlement": fd["settlement"][0] if n > 0 else None,
            "front_return_1d": fd["return_1d"][0] if n > 0 else None,
            "days_to_expiry": fd["days_to_expiry"][0] if n > 0 else None,
            "m1_m2_spread": front_spread,
            "curve_slope_per_month": fd["front_back_slope"][0] if n > 0 else None,
            "contango": fd["contango_flag"][0] if n > 0 else None,
            "active_contracts": n,
            "feature_source": "gold/futures_features",
        }
        section["status"] = "available"

    if gold_options is not None and len(gold_options) > 0:
        od = gold_options.to_pydict()
        expiries = sorted(set(od["option_expiry"]))
        atm_ivs   = [v for v in od["atm_iv"] if v is not None]
        rr25_vals = [v for v in od["risk_reversal_25d"] if v is not None]
        bf25_vals = [v for v in od["butterfly_25d"] if v is not None]
        skew_vals = [v for v in od["skew_slope"] if v is not None]
        section["options"] = {
            "expiries_covered": expiries[:6],
            "atm_iv": atm_ivs[0] if atm_ivs else None,
            "risk_reversal_25d": rr25_vals[0] if rr25_vals else None,
            "butterfly_25d": bf25_vals[0] if bf25_vals else None,
            "skew_slope": skew_vals[0] if skew_vals else None,
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
        "spr_stocks_mbbl": d.get("spr_stocks", [None])[0],
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


def _catalysts_section(top_catalysts: list[dict], themes: Optional[list] = None) -> dict:
    # Rank is the position in the globally score-sorted list (post dedup/decay).
    # The stored relevance_rank is per-extraction-batch and collides across
    # batches (multiple runs per day produced 1,1,2,1,2 in the brief).
    return {
        "count": len(top_catalysts),
        "themes": themes or [],
        "top_events": [
            {
                "rank": i + 1,
                "score": e.get("decayed_score", e.get("relevance_score")),
                "raw_score": e.get("relevance_score"),
                "decay_days": e.get("decay_days"),
                "sources": e.get("duplicate_count", 1),
                "event_id": e.get("event_id"),
                "title": e.get("title"),
                "direction": e.get("direction"),
                "magnitude": e.get("magnitude"),
                "affected_horizon": e.get("affected_horizon"),
                "effective_start": e.get("effective_start"),
                "source_url": e.get("source_url"),
            }
            for i, e in enumerate(top_catalysts[:5])
        ],
    }


def _term_structure_section(gold_futures, gold_options) -> dict:
    """Vol + futures term structure (C4) from tables already in hand."""
    out: dict = {"status": "unavailable"}

    # Vol strip: one row per expiry (surface metrics are constant within expiry)
    if gold_options is not None and len(gold_options) > 0:
        od = gold_options.to_pydict()
        seen: dict[str, dict] = {}
        for i in range(len(od["option_expiry"])):
            exp = od["option_expiry"][i]
            if exp not in seen and od["atm_iv"][i] is not None:
                seen[exp] = {"expiry": exp, "atm_iv": od["atm_iv"][i],
                             "rr25": od["risk_reversal_25d"][i]}
        strip = [seen[e] for e in sorted(seen)][:6]
        out["vol_strip"] = strip
        if len(strip) >= 2 and strip[0]["atm_iv"] and strip[1]["atm_iv"]:
            out["front_2nd_iv_spread"] = strip[0]["atm_iv"] - strip[1]["atm_iv"]
        out["status"] = "available"

    # Futures spreads: M1−M3/M6/M12 + annualized front roll yield
    if gold_futures is not None and len(gold_futures) > 0:
        fd = gold_futures.to_pydict()
        settles = fd["settlement"]
        codes = fd["contract_code"]
        m1 = settles[0] if settles else None

        def _spread(n):
            return (m1 - settles[n], codes[n]) if m1 is not None and len(settles) > n \
                and settles[n] is not None else (None, None)

        for label, n in (("m1_m3", 2), ("m1_m6", 5), ("m1_m12", 11)):
            v, code = _spread(n)
            out[label] = v
            out[f"{label}_code"] = code
        if m1 and len(settles) > 1 and settles[1]:
            # positive = backwardation = positive carry for a rolling long
            out["roll_yield_annualized"] = (m1 - settles[1]) / m1 * 12.0
        out["status"] = "available"
    return out


def _history_context_section(ctx) -> dict:
    """Percentiles/z-scores of headline metrics vs accumulated gold history (B2)."""
    if ctx is None or len(ctx) == 0:
        return {"status": "unavailable"}
    d = ctx.to_pydict()
    keys = [
        "lookback_days",
        "front_settle_pctile", "front_settle_z",
        "settle_30d_high", "settle_30d_low", "settle_range_position",
        "curve_slope_pctile", "curve_slope_z",
        "m1_m2_pctile", "m1_m2_z",
        "atm_iv_pctile", "atm_iv_z",
        "rr25_pctile", "rr25_z",
        "bf25_pctile", "bf25_z",
        "skew_slope_pctile", "skew_slope_z",
        "realized_vol_10d", "realized_vol_21d", "vrp_10d", "vrp_21d",
        "brent_front", "brent_wti_spread", "brent_wti_pctile", "brent_wti_z",
    ]
    out = {k: d[k][0] for k in keys if k in d}
    out["status"] = "available"
    return out


def _quality_notes(quality_report: dict) -> str:
    """One-line explanation of a non-PASS overall status (A6).

    e.g. "futures WARN (10 rows have warnings); options WARN (7 rows failed
    quality checks)" — so the brief header never shows a bare WARN.
    """
    if quality_report.get("overall_status", "PASS") == "PASS":
        return ""
    parts = []
    for section in ("futures", "options", "fundamentals"):
        s = quality_report.get(section, {})
        status = s.get("status", "PASS")
        if status != "PASS":
            notes = s.get("notes") or []
            first = f" ({notes[0]})" if notes else ""
            parts.append(f"{section} {status}{first}")
    return "; ".join(parts)


def _caveats(quality_report: dict) -> list[str]:
    caveats = [
        "settlement_data_only: prices are EOD settlements, not executable quotes",
        "baw_iv: implied vol uses Barone-Adesi & Whaley (1987) for American option pricing; "
        "Black-76 retained as reference only",
        "eia_lag: EIA weekly data has a 1-week lag (e.g. Thu report covers prior week)",
        "expiry_convention_corrected_2026-07-10: LO option expiry now uses the exchange "
        "rule (futures LTD − 3 business days, holiday-aware) instead of a 3rd-Friday "
        "approximation; all IV/greeks history was restated (front-expiry TTE shortened "
        "~4 days → slightly higher IVs than previously reported)",
    ]
    # Delta quality check: bulletin-published deltas vs BAW model deltas (A7).
    # An empirical validation of the pricing setup (TTE, model, rate).
    dc = quality_report.get("delta_check")
    if dc and dc.get("compared"):
        caveats.append(
            f"delta_check: {dc.get('status')} — model delta vs CME-published delta, "
            f"{dc['compared']} options compared, mean|diff|={dc.get('mean_abs_delta_diff', 0):.4f}, "
            f"max|diff|={dc.get('max_abs_delta_diff', 0):.3f}"
        )
    qr_caveats = quality_report.get("caveats", [])
    caveats.extend(c for c in qr_caveats if c not in caveats)
    return caveats


def _next_review(trade_date: date, top_catalysts: list[dict]) -> dict:
    # Scheduled events from the knowledge-pack calendar (B1): weekly releases,
    # dated one-offs, and contract expiry/LTD dates within the horizon.
    try:
        from ..knowledge.loader import upcoming_events
        scheduled = upcoming_events(trade_date, horizon_days=8)
    except Exception as exc:  # knowledge pack missing/unparseable — degrade
        logger_scheduled_error = f"knowledge calendar unavailable: {exc}"
        scheduled = [{"date": None, "name": logger_scheduled_error,
                      "time_et": None, "kind": "error"}]

    # Nearest catalyst start date (unchanged)
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
        "scheduled_events": scheduled,
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

    quality_notes = report.get("quality_notes", "")
    quality_suffix = f" — {quality_notes}" if quality_notes else ""
    lines = [
        f"# CurveLens Daily Forward-Risk Brief — {td}",
        f"\n*Generated: {gen}  |  Data quality: **{quality}**{quality_suffix}*",
        "",
    ]

    # ── What changed since the prior session (D3) ──
    wc = s.get("what_changed", {})
    diff, streaks = wc.get("diff", {}), wc.get("streaks", {})
    if diff.get("status") == "available":
        def _d(key, fmt, scale=1.0):
            v = diff.get(key)
            return fmt.format(v * scale) if v is not None else "n/a"

        lines += [f"## What Changed *(vs {diff.get('prior_date')})*", ""]
        lines.append(
            f"- Front settle {_d('settle_change', '{:+.2f}$')} "
            f"| ATM IV {_d('atm_iv_change', '{:+.1f}pp', 100)} "
            f"| 25Δ RR {_d('rr25_change', '{:+.1f}pp', 100)} "
            f"| slope {_d('slope_change', '{:+.3f}$/mo')}"
        )
        agr_t, agr_p = diff.get("agreement_today"), diff.get("agreement_prior")
        if agr_t and agr_p:
            lines.append(
                f"- Agreement: `{agr_p}` → `{agr_t}`" if agr_t != agr_p
                else f"- Agreement unchanged: `{agr_t}`"
                     + (f" ({streaks.get('agreement_state_streak_days')} sessions)"
                        if streaks.get("agreement_state_streak_days") else "")
            )
        for tr in diff.get("scenario_transitions") or []:
            lines.append(f"- Scenario transition: **{tr}**")
        streak_bits = []
        if streaks.get("consecutive_eia_draw_weeks"):
            streak_bits.append(f"{streaks['consecutive_eia_draw_weeks']} consecutive EIA draw week(s)")
        if streaks.get("days_in_backwardation"):
            streak_bits.append(f"{streaks['days_in_backwardation']} session(s) in backwardation")
        if streak_bits:
            lines.append(f"- Streaks: {' · '.join(streak_bits)}")
        lines.append("")

    # ── Section 1: Market Risk ──
    lines += ["## 1. Current Market-Implied Risk", ""]
    if fut:
        slope = fut.get("curve_slope_per_month")
        slope_str = f"${slope:+.3f}/month" if slope is not None else "N/A"
        m1m2 = fut.get("m1_m2_spread")
        m1m2_str = f"${m1m2:+.3f}" if m1m2 is not None else "N/A"
        ret1d = fut.get("front_return_1d")
        ret1d_str = f"{ret1d:+.2%}" if ret1d is not None else "N/A"
        dte = fut.get("days_to_expiry")
        structure = "CONTANGO" if fut.get("contango") else "BACKWARDATION"
        lines += [
            f"**{_product_display()} Futures** (source: `{fut.get('feature_source')}`)",
            f"- Front contract: **{fut.get('front_contract')}** @ **{_fmt_usd(fut.get('front_settlement'))}/bbl**"
            + (f"  ({ret1d_str} 1-day)" if ret1d is not None else ""),
            f"- Days to expiry: **{dte}**" if dte is not None else "",
            f"- Curve structure: **{structure}** (slope: {slope_str}  |  M1-M2 spread: {m1m2_str})",
            f"- Active contracts: {fut.get('active_contracts')}",
            "",
        ]
    if opt:
        bf25 = opt.get("butterfly_25d")
        bf25_str = f"{bf25:.2%}" if bf25 is not None else "N/A"
        skew = opt.get("skew_slope")
        skew_str = f"{skew:.3f}" if skew is not None else "N/A"
        lines += [
            f"**Options Surface — BAW IV** (source: `{opt.get('feature_source')}`)",
            f"- ATM IV: **{_fmt_pct(opt.get('atm_iv'))}**",
            f"- 25Δ Risk Reversal: **{_fmt_pct(opt.get('risk_reversal_25d'))}**",
            f"- 25Δ Butterfly: **{bf25_str}**  |  Skew slope: **{skew_str}**",
            f"- ⚠ Note: *{opt.get('coverage_note')}*",
            "",
        ]

    # ── History context (percentiles vs accumulated gold) ──
    ctx = s.get("history_context", {})
    if ctx.get("status") == "available":
        n = ctx.get("lookback_days")

        def _pc(key):
            v = ctx.get(key)
            return f"{v:.0f}%ile" if v is not None else "n/a"

        rng = ctx.get("settle_range_position")
        rng_str = f"{rng:.0%} of 30d range" if rng is not None else "n/a"
        lines += [
            f"**Context** *(vs {n} trade dates of history — percentiles firm up as history accrues)*",
            f"- Settle: {_pc('front_settle_pctile')} ({rng_str}, "
            f"30d band {_fmt_usd(ctx.get('settle_30d_low'))}–{_fmt_usd(ctx.get('settle_30d_high'))})",
            f"- ATM IV: {_pc('atm_iv_pctile')}  |  25Δ RR: {_pc('rr25_pctile')}  |  25Δ BF: {_pc('bf25_pctile')}",
            f"- Curve slope: {_pc('curve_slope_pctile')}  |  M1-M2: {_pc('m1_m2_pctile')}",
        ]
        # Realized vs implied (B6): constant-contract RV, VRP = IV − RV
        rv10, vrp10 = ctx.get("realized_vol_10d"), ctx.get("vrp_10d")
        rv21, vrp21 = ctx.get("realized_vol_21d"), ctx.get("vrp_21d")
        if rv10 is not None or rv21 is not None:
            def _rv(rv, vrp, label):
                if rv is None:
                    return None
                v = f" (VRP {vrp*100:+.1f}pp)" if vrp is not None else ""
                return f"RV {label}: {rv:.1%}{v}"
            bits = [b for b in (_rv(rv10, vrp10, "10d"), _rv(rv21, vrp21, "21d")) if b]
            lines.append(f"- Realized vs implied: {'  |  '.join(bits)} — positive VRP = IV rich to realized")
        # Brent–WTI spread (B5): front-continuous Brent vs WTI front settle
        bw = ctx.get("brent_wti_spread")
        if bw is not None:
            bw_pc = ctx.get("brent_wti_pctile")
            pc_str = f" ({bw_pc:.0f}%ile)" if bw_pc is not None else ""
            lines.append(f"- Brent–WTI: {bw:+.2f}$/bbl{pc_str} *(front-continuous approx)*")
        lines.append("")

    # ── Options positioning: open interest (C2) ──
    oi_sec = s.get("oi") or {}
    for ex in (oi_sec.get("expiries") or [])[:1]:  # front expiry in the brief
        pc = ex.get("put_call_oi_ratio")
        mp = ex.get("max_pain")
        vol_oi = ex.get("volume_oi_ratio")
        cw = ", ".join(f"${w['strike']:.0f} ({w['oi']/1000:.1f}k)" for w in ex.get("call_walls", []))
        pw = ", ".join(f"${w['strike']:.0f} ({w['oi']/1000:.1f}k)" for w in ex.get("put_walls", []))
        doi = ", ".join(f"{d['delta_oi']:+,} {d['cp']}${d['strike']:.0f}"
                        for d in ex.get("top_delta_oi", []))
        lines += [
            f"**Options Positioning — {ex.get('expiry')}** *(source: bulletin OI)*",
            f"- Put/Call OI: **{pc}**  |  Total OI: {(ex.get('call_oi',0)+ex.get('put_oi',0))/1000:.0f}k"
            f"  |  Volume/OI: {vol_oi}  |  Max pain: **{_fmt_usd(mp)}**",
            f"- Call walls: {cw or 'n/a'}",
            f"- Put walls: {pw or 'n/a'}",
        ]
        if doi:
            lines.append(f"- Largest ΔOI: {doi}")
        lines.append("")

    # ── Futures positioning: CFTC COT (B3) ──
    cot_sec = s.get("cot") or {}
    if cot_sec.get("mm_net") is not None:
        wow = cot_sec.get("mm_net_wow")
        wow_str = f"{wow:+,}" if wow is not None else "n/a"
        p1 = cot_sec.get("mm_net_pctile_1y")
        p1_str = f"{p1:.0f}%ile 1y" if p1 is not None else "n/a"
        lines += [
            f"**Futures Positioning — CFTC COT** *(report {cot_sec.get('report_date')}; "
            f"{cot_sec.get('published_note')})*",
            f"- Managed money net: **{cot_sec['mm_net']:+,}** lots ({wow_str} WoW, {p1_str})",
            f"- Producer/merchant net: {cot_sec.get('prod_net', 0):+,}  |  "
            f"Total OI: {cot_sec.get('open_interest', 0):,}",
            "",
        ]

    # ── Market-implied distribution (C3, Breeden–Litzenberger) ──
    rnd_sec = s.get("rnd") or {}
    for ex in (rnd_sec.get("expiries") or [])[:1]:  # front expiry in the brief
        em = ex.get("expected_move_straddle")
        em_str = f"±${em:.2f}" if em is not None else "n/a"
        ladder = ex.get("prob_ladder") or {}
        ladder_str = " · ".join(
            f"P(>${k.split('_')[-1]}): {v:.0%}" for k, v in sorted(
                ladder.items(), key=lambda kv: float(kv[0].split("_")[-1]))
        )
        lines += [
            f"**Market-Implied Distribution — {ex.get('expiry')}** "
            f"*(Breeden–Litzenberger on the settlement curve)*",
            f"- Expected move to expiry: **{em_str}** (straddle)  |  "
            f"RN σ: ${ex.get('rn_std'):.2f}  |  RN skew: {ex.get('rn_skew'):+.2f}",
        ]
        if ladder_str:
            lines.append(f"- {ladder_str}")
        lines.append(f"- *diagnostics: {ex.get('n_strikes')} strikes, "
                     f"raw mass {ex.get('raw_mass'):.2f} (→1.00 = clean grid)*")
        lines.append("")

    # ── Term structure (C4) ──
    ts = s.get("term_structure") or {}
    if ts.get("status") == "available":
        strip = ts.get("vol_strip") or []
        if strip:
            iv_str = " → ".join(f"{r['expiry'][5:]}: {r['atm_iv']:.1%}" for r in strip)
            rr_str = " / ".join(f"{r['rr25']:+.1%}" if r.get("rr25") is not None else "n/a"
                                for r in strip)
            f2 = ts.get("front_2nd_iv_spread")
            f2_str = f"  *(front−2nd: {f2*100:+.1f}pp)*" if f2 is not None else ""
            lines += ["**Term Structure**",
                      f"- ATM IV strip: {iv_str}{f2_str}",
                      f"- 25Δ RR by expiry: {rr_str}"]
        spreads = []
        for label, key in (("M1−M3", "m1_m3"), ("M1−M6", "m1_m6"), ("M1−M12", "m1_m12")):
            v = ts.get(key)
            if v is not None:
                spreads.append(f"{label}: {v:+.2f}")
        ry = ts.get("roll_yield_annualized")
        if ry is not None:
            spreads.append(f"roll yield: {ry:+.1%}/yr")
        if spreads:
            lines.append(f"- Futures: {'  |  '.join(spreads)}")
        lines.append("")

    # ── Section 2: EIA Fundamentals ──
    lines += ["## 2. EIA Weekly Fundamentals", ""]
    if eia.get("status") == "available":
        draw = eia.get("crude_draw_mbbl")
        cush_draw = eia.get("cushing_draw_mbbl")
        util = eia.get("refinery_utilization_pct")
        signal = eia.get("supply_signal", "neutral").upper()
        trigger = eia.get("scenario_trigger", "none")
        # draw columns store -(wow_change): negate to show actual stock WoW change
        draw_str = f"{-draw:+,.0f} MBBL" if draw is not None else "N/A"
        cush_str = f"{-cush_draw:+,.0f} MBBL" if cush_draw is not None else "N/A"
        util_str = f"{util:.1f}%" if util is not None else "N/A"
        net_imp = eia.get("net_imports_mbbld")
        net_str = f"{net_imp:+.0f} MBBL/D" if net_imp is not None else "N/A"
        def _mbbl(v):
            return f"{v:,.0f} MBBL" if v is not None else "N/A"
        def _mbbld_wow(v):
            # draw columns = -(wow_change); negate to show actual stock change
            return f"{-v:+,.0f} MBBL" if v is not None else "N/A"
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
            f"| SPR stocks | {_mbbl(eia.get('spr_stocks_mbbl'))} | — |",
            f"| Cushing stocks (WTI delivery) | {cush_val} | {cush_str} |",
            f"| Refinery utilization | {util_str} | — |",
            f"| Net imports | {net_str} | — |",
            f"| Gasoline stocks | {gas_val} | {gas_wow} |",
            f"| Distillate stocks | {dist_val} | {dist_wow} |",
            "",
            f"**Supply signal:** `{signal}`  |  **Cushing signal:** `{eia.get('cushing_signal','—').upper()}`  |  **Scenario trigger:** `{trigger}`",
            "",
        ]
        # Seasonal context (B4): judge the print vs the 5y week-of-year norm
        seas = s.get("eia_seasonal") or {}
        if seas.get("seasonal_available"):
            surp = seas["surprise_draw_mbbl"]
            lvl = seas.get("level_vs_5y_avg_mbbl")
            lvl_str = f"; stocks {lvl:+,.0f} MBBL vs 5-yr avg level" if lvl is not None else ""
            disagree = (f" — *seasonal trigger `{seas['trigger']}` overrides fixed "
                        f"`{seas['fixed_trigger']}`*" if seas.get("disagrees_with_fixed") else "")
            lines += [
                f"**vs 5-yr seasonal:** draw {seas['actual_draw_mbbl']:+,.0f} vs seasonal-avg "
                f"draw {seas['seasonal_avg_draw_mbbl']:+,.0f} → **surprise {surp:+,.0f} MBBL** "
                f"(n={seas['seasonal_n_samples']}{lvl_str}){disagree}",
                "",
            ]
    else:
        lines.append("*EIA data not available for this date.*\n")

    # ── Section 3: Catalysts ──
    lines += ["## 3. Upcoming Catalysts", ""]
    if cats["count"] == 0:
        lines.append("*No catalyst events on record for this date.*\n")
    else:
        lines.append(f"*{cats['count']} distinct event(s) after dedup; "
                     f"scores decay 5%/day past the event start.*\n")
        # Theme summary (C5)
        themes = cats.get("themes") or []
        if themes:
            theme_str = " · ".join(
                f"{t['event_type']}/{t['direction']} ×{t['count']}" for t in themes[:5])
            lines.append(f"**Themes:** {theme_str}\n")
        lines.append("| # | Score | Src | Direction | Title | Horizon |")
        lines.append("|---|-------|-----|-----------|-------|---------|")
        for ev in cats["top_events"]:
            title = (ev.get("title") or "")[:60]
            score = ev.get("score")
            decay = ev.get("decay_days")
            score_str = f"{score}" + (f" (−{decay}d)" if decay else "")
            lines.append(
                f"| {ev.get('rank')} | {score_str} | ×{ev.get('sources', 1)} | "
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
        # A5: the old "Curve P&L estimate: sum of shocks × 1 contract each" was
        # a placeholder that implied a position nobody holds — show the honest
        # per-contract quantities instead.
        avg_shift = sc.get("avg_curve_shift")
        avg_str = f"  |  **Avg curve shift:** {avg_shift:+.2f} $/bbl" if avg_shift is not None else ""
        lines += [
            f"### {name}: {desc}",
            f"- **Front-month impact:** {fi:+.2f} $/bbl{avg_str}",
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

    # ── Section 5: Triggers — evaluated daily (C1) ──
    lines += ["## 6. Confirmation / Invalidation Triggers", ""]
    monitor = s.get("monitor") or {}
    trig_results = monitor.get("trigger_results") or []
    scenario_states = monitor.get("scenarios") or {}
    if trig_results:
        lines.append("*Evaluated against gold history each run — "
                     "✅ fired · ▢ not fired · ◌ not evaluable/manual*")
        lines.append("")
        for sc_name in ("bull", "base", "bear"):
            st = scenario_states.get(sc_name, {})
            status = st.get("status", "live").upper()
            since = st.get("since", "")
            fired_c = len(st.get("confirms_fired", []))
            total_c = st.get("confirms_total_auto", 0)
            lines.append(f"**{sc_name.upper()}: {status}** *(since {since} · "
                         f"{fired_c}/{total_c} confirms fired)*")
            for r in trig_results:
                if r["scenario"] != sc_name:
                    continue
                icon = "✅" if r["fired"] is True else ("▢" if r["fired"] is False else "◌")
                side = "Confirms" if r["side"] == "confirm" else "Invalidates"
                lines.append(f"- {icon} {side}: {r['description']}")
            lines.append("")
    else:
        # Fallback: static prose triggers (monitor data unavailable)
        for sc in scenarios[:3]:  # bull/base/bear
            name = sc.get("name", "").upper()
            lines.append(f"**{name}**")
            for t in sc.get("confirmation_triggers", []):
                lines.append(f"- ✅ Confirms: {t}")
            for t in sc.get("invalidation_triggers", []):
                lines.append(f"- ❌ Invalidates: {t}")
            lines.append("")

    # ── Calibration scorecard (C7) — rendered once samples accumulate ──
    sc_card = s.get("scorecard") or {}
    if sc_card.get("render_ready"):
        lines += ["## Calibration Scorecard",
                  f"\n*Agreement-state hit rates over {sc_card.get('dates_covered')} "
                  f"trade dates — how often each state preceded a move in its "
                  f"direction (3-session forward).*", "",
                  "| State | n | fwd 1d | fwd 3d | fwd 5d | hit (3d) |",
                  "|-------|---|--------|--------|--------|----------|"]
        for r in sc_card.get("states", []):
            def _r(key):
                v = r.get(key)
                return f"{v:+.2%}" if v is not None else "—"
            hit = r.get("hit_rate_3d")
            hit_str = f"{hit:.0%} (n={r.get('n_hits_3d')})" if hit is not None else "—"
            lines.append(f"| `{r['state']}` | {r['n']} | {_r('avg_fwd_1d')} | "
                         f"{_r('avg_fwd_3d')} | {_r('avg_fwd_5d')} | {hit_str} |")
        lines.append("")

    # ── Section 6: Caveats ──
    lines += ["## 7. Data Caveats", ""]
    for c in caveats:
        lines.append(f"- {c}")
    lines.append("")

    # ── Section 7: Next Review ──
    lines += ["## 8. Next Review", ""]
    scheduled = next_rev.get("scheduled_events") or []
    if scheduled:
        lines.append("**Scheduled** *(from knowledge/wti/calendar.yaml + contract calendar)*")
        for ev in scheduled:
            when = ev.get("date") or "?"
            t = f" {ev['time_et']} ET" if ev.get("time_et") else ""
            lines.append(f"- {when}{t} — {ev.get('name')} `{ev.get('kind')}`")
        lines.append("")
    lines += [
        f"- **Next catalyst date:** {next_rev.get('next_catalyst_date', 'N/A')} — {next_rev.get('next_catalyst_title', '')}",
        "",
        "---",
        f"*CurveLens — {_product_display()} futures and options daily analytics. "
        "Settlement data only; does not establish executability or confirmed mispricing.*",
    ]

    return "\n".join(lines) + "\n"

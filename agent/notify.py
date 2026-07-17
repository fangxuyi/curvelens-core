#!/usr/bin/env python
"""CurveLens alert preparation + delivery queue.

Mirrors the RegWatch delivery model: this script NEVER talks to Telegram. It
formats ready-to-send messages from the day's report and queues them in
data/agent_outbox/pending.json. The standalone CurveLens agent reads the queue
(--list-pending), delivers each message via its own Telegram integration, and
acknowledges what it sent (--ack) so nothing is delivered twice.

Two message types are produced from one report:
    DAILY_BRIEF     always — a compact digest of the day's forward-risk brief
    PRIORITY_ALERT  only when the day is alert-worthy (confirmed directional
                    agreement, or an EIA bull/bear-confirmed scenario)

Message ids are deterministic ("<date>:<type>"), so a given date can queue each
type at most once — re-running --prepare is idempotent and will not re-queue a
message that was already delivered.

Usage:
    python agent/notify.py --is-new 2026-07-02        # freshness gate (before saving PDF)
    python agent/notify.py --prepare --date 2026-07-02
    python agent/notify.py --list-pending
    python agent/notify.py --ack 2026-07-02:DAILY_BRIEF
    python agent/notify.py --ack-all
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# This script lives in CurveLens/agent/; the pipeline's data lives in
# CurveLens/ccvm/data/.
REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "ccvm" / "data"
sys.path.insert(0, str(REPO_ROOT / "ccvm" / "src"))
OUTBOX_DIR = DATA_DIR / "agent_outbox"
PENDING_PATH = OUTBOX_DIR / "pending.json"
DELIVERED_PATH = OUTBOX_DIR / "delivered.json"

_ALERT_STATES = {"confirmed_upside_risk", "confirmed_downside_risk"}
_ALERT_SCENARIOS = {"bull_confirmed", "bear_confirmed"}


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


def _save(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(obj: dict) -> None:
    print(json.dumps(obj))


# ── Message formatting ─────────────────────────────────────────────────────

def _fmt_signed(v, unit="", scale=1.0, decimals=2):
    if v is None:
        return "n/a"
    return f"{v*scale:+.{decimals}f}{unit}"


def _product():
    from ccvm.reference.product import get_product
    return get_product()


def _currency_amount(v, decimals: int = 2) -> str:
    if v is None:
        return "n/a"
    product = _product()
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(product.currency)
    return (f"{symbol}{v:,.{decimals}f}" if symbol
            else f"{v:,.{decimals}f} {product.currency}")


def _price(v, decimals: int = 2) -> str:
    if v is None:
        return "n/a"
    product = _product()
    amount = _currency_amount(v, decimals)
    denominator = (f"/{product.price_unit.split('/', 1)[1].lower()}"
                   if "/" in product.price_unit else f" {product.price_unit}")
    return amount + denominator


def _price_change(v, decimals: int = 2, include_unit: bool = False) -> str:
    if v is None:
        return "n/a"
    product = _product()
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(product.currency)
    sign = "+" if v >= 0 else "-"
    amount = (f"{sign}{symbol}{abs(v):,.{decimals}f}" if symbol
              else f"{sign}{abs(v):,.{decimals}f} {product.currency}")
    if not include_unit:
        return amount
    denominator = (f"/{product.price_unit.split('/', 1)[1].lower()}"
                   if "/" in product.price_unit else f" {product.price_unit}")
    return amount + denominator


def _fmt_pct(v, decimals=1):
    return "n/a" if v is None else f"{v * 100:.{decimals}f}%"


def _fmt_pp(v, decimals=1):
    return "n/a" if v is None else f"{v * 100:+.{decimals}f}pp"


def _short(text, limit=96):
    if not text:
        return ""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _ordinal(n) -> str:
    if n is None:
        return "n/a"
    whole = int(round(n))
    suffix = ("th" if 10 <= whole % 100 <= 20
              else {1: "st", 2: "nd", 3: "rd"}.get(whole % 10, "th"))
    return f"{whole}{suffix}"


def _state_label(state: str) -> str:
    labels = {
        "confirmed_upside_risk": "confirmed upside risk",
        "confirmed_downside_risk": "confirmed downside risk",
        "non_directional_uncertainty": "non-directional uncertainty",
        "insufficient_data": "insufficient data",
    }
    return labels.get(state, state.replace("_", " ") if state else "n/a")


def _scenario_label(scenario: str) -> str:
    labels = {
        "bull_confirmed": "bull confirmed", "bear_confirmed": "bear confirmed",
        "bull_watch": "bull watch", "bear_watch": "bear watch", "none": "none",
    }
    return labels.get(scenario, scenario.replace("_", " ") if scenario else "none")


def _fmt_inventory_change(v):
    if v is None:
        return "n/a"
    if v > 0:
        return f"draw {v:,.0f} MBBL"
    if v < 0:
        return f"build {abs(v):,.0f} MBBL"
    return "flat"


def _fundamentals_label() -> str:
    from ccvm.fundamentals import get_provider
    provider = get_provider(_product().fundamentals_provider)
    return provider.display_name if provider else "Fundamentals"


def _history_line(ctx: dict) -> str:
    if not ctx or ctx.get("status") != "available":
        return ""
    benchmark = ctx.get("benchmark_name")
    if not benchmark and _product().benchmark:
        benchmark = _product().benchmark.name
    mapping = [
        ("front_settle_pctile", "settle"), ("m1_m2_pctile", "M1-M2"),
        ("atm_iv_pctile", "ATM IV"), ("rr25_pctile", "RR25"),
        ("benchmark_spread_pctile", f"{benchmark} spread" if benchmark else "benchmark spread"),
    ]
    parts = [f"{label} {_ordinal(ctx[key])} %ile"
             for key, label in mapping if ctx.get(key) is not None]
    if ctx.get("settle_30d_high") is not None and ctx.get("settle_30d_low") is not None:
        parts.append(
            f"30d band {_currency_amount(ctx['settle_30d_low'])}-"
            f"{_currency_amount(ctx['settle_30d_high'])}"
        )
    return "; ".join(parts[:5])


def _extreme_context(ctx: dict) -> list[str]:
    if not ctx or ctx.get("status") != "available":
        return []
    benchmark = ctx.get("benchmark_name") or (
        _product().benchmark.name if _product().benchmark else "benchmark"
    )
    labels = {
        "front_settle_pctile": "settle", "m1_m2_pctile": "M1-M2",
        "curve_slope_pctile": "curve slope", "atm_iv_pctile": "ATM IV",
        "rr25_pctile": "RR25", "bf25_pctile": "25Δ fly",
        "skew_slope_pctile": "skew slope", "benchmark_spread_pctile": f"{benchmark} spread",
    }
    return [f"{label} {_ordinal(ctx[key])} %ile" for key, label in labels.items()
            if ctx.get(key) is not None and (ctx[key] >= 90 or ctx[key] <= 10)][:4]


def _next_event(next_review: dict) -> str:
    events = next_review.get("scheduled_events") or []
    if not events:
        return ""
    event = events[0]
    when = event.get("date") or "date TBD"
    if event.get("time_et"):
        when += f" {event['time_et']} ET"
    return f"{when}: {event.get('name', 'scheduled event')}"


def _scenario_statuses(monitor: dict) -> str:
    parts = []
    for name in ("bull", "base", "bear"):
        data = (monitor.get("scenarios") or {}).get(name) or {}
        if data.get("status"):
            suffix = f" since {data['since']}" if data.get("since") else ""
            parts.append(f"{name} {data['status']}{suffix}")
    return "; ".join(parts)


def _fired_trigger_descriptions(monitor: dict, scenario: str) -> list[str]:
    return [result.get("description") or result.get("id")
            for result in monitor.get("trigger_results") or []
            if result.get("scenario") == scenario
            and result.get("side") == "confirm" and result.get("fired") is True]


def _scenario_level(scenarios: list[dict], direction: str) -> str:
    target = "bull" if direction == "UPSIDE" else "bear"
    scenario = next((item for item in scenarios or [] if item.get("name") == target), None)
    if not scenario:
        return ""
    front = ((scenario.get("shocked_settlements") or [{}])[0])
    if front.get("base_settlement") is None or front.get("shocked_settlement") is None:
        return ""
    return (f"{target} case front settle {_price(front['base_settlement'])} → "
            f"{_price(front['shocked_settlement'])} "
            f"({_price_change(front.get('diff'), include_unit=True)})")


def _scorecard_line(scorecard: dict, state: str) -> str:
    if not scorecard or not scorecard.get("render_ready"):
        return ""
    row = next((item for item in scorecard.get("states") or []
                if item.get("state") == state), None)
    if not row:
        return ""
    parts = [f"n={row.get('n')}"]
    if row.get("avg_fwd_3d") is not None:
        parts.append(f"avg 3d fwd {_fmt_signed(row['avg_fwd_3d'], '%', 100)}")
    if row.get("hit_rate_3d") is not None:
        parts.append(f"3d hit {_fmt_pct(row['hit_rate_3d'], 0)}")
    return ", ".join(parts)


def _front_oi_line(oi: dict) -> str:
    expiries = oi.get("expiries") or []
    if not expiries:
        return ""
    expiry = expiries[0]
    bits = []
    if expiry.get("put_call_oi_ratio") is not None:
        bits.append(f"put/call OI {expiry['put_call_oi_ratio']:.2f}")
    if expiry.get("max_pain") is not None:
        bits.append(f"max pain {_currency_amount(expiry['max_pain'], 0)}")
    walls = []
    call_wall = (expiry.get("call_walls") or [{}])[0]
    put_wall = (expiry.get("put_walls") or [{}])[0]
    if call_wall.get("strike") is not None:
        walls.append(f"call wall {_currency_amount(call_wall['strike'], 0)}")
    if put_wall.get("strike") is not None:
        walls.append(f"put wall {_currency_amount(put_wall['strike'], 0)}")
    if walls:
        bits.append(", ".join(walls))
    return (f"{expiry.get('expiry', 'front expiry')}: " + "; ".join(bits)) if bits else ""


def _daily_brief_text(date_str: str, sections: dict, agreement: dict) -> str:
    mr = sections.get("market_risk", {})
    fut = mr.get("futures", {}) or {}
    opt = mr.get("options", {}) or {}
    fundamentals = sections.get("fundamentals", sections.get("eia_fundamentals", {})) or {}
    cats = sections.get("catalysts", {}) or {}
    changed = sections.get("what_changed", {}) or {}
    diff = changed.get("diff", {}) or {}
    streaks = changed.get("streaks", {}) or {}
    term = sections.get("term_structure", {}) or {}
    history = sections.get("history_context", {}) or {}
    oi = sections.get("oi", {}) or {}
    monitor = sections.get("monitor", {}) or {}
    next_review = sections.get("next_review", {}) or {}

    lines = [f"📈 *CurveLens Daily Brief — {date_str}*", "",
             "_Routine settlement digest. Priority alerts are sent separately when triggers fire._", ""]

    # Futures line
    if fut:
        code = fut.get("front_contract", "?")
        settle = fut.get("front_settlement")
        ret = fut.get("front_return_1d")
        struct = "contango" if fut.get("contango") else "backwardation"
        change = diff.get("settle_change") if diff.get("status") == "available" else None
        change_s = f", {_price_change(change)} d/d" if change is not None else ""
        dte = (f", {fut['days_to_expiry']}d to expiry"
               if fut.get("days_to_expiry") is not None else "")
        lines.append(f"*Front:* {code} {_price(settle)} "
                     f"({_fmt_signed(ret, '%', 100)} 1d{change_s}){dte}")
        lines.append(
            f"*Curve:* {struct}; M1-M2 {_price_change(fut.get('m1_m2_spread'))}; "
            f"M1-M6 {_price_change(term.get('m1_m6'))}; "
            f"roll {_fmt_signed(term.get('roll_yield_annualized'), '%', 100, 1)} ann."
        )

    # Options line
    if opt:
        atm = opt.get("atm_iv")
        rr = opt.get("risk_reversal_25d")
        atm_change = diff.get("atm_iv_change") if diff.get("status") == "available" else None
        change_s = f" ({_fmt_pp(atm_change)} d/d)" if atm_change is not None else ""
        lines.append(f"*Vol:* front ATM {_fmt_pct(atm)}{change_s}; "
                     f"25Δ RR {_fmt_signed(rr, '%', 100)}; "
                     f"front/2nd IV {_fmt_pp(term.get('front_2nd_iv_spread'))}")

    context = _history_line(history)
    if context:
        lines.append(f"*Context:* {context}")

    # EIA line
    if fundamentals and fundamentals.get("status") == "available":
        label = _fundamentals_label()
        draw = fundamentals.get("crude_draw_mbbl")
        if draw is not None:  # EIA weekly petroleum provider payload
            seasonal = sections.get("eia_seasonal", {}) or {}
            lines.append(
                f"*{label}:* {fundamentals.get('eia_period', 'n/a')} crude "
                f"{_fmt_inventory_change(draw)}; Cushing "
                f"{_fmt_inventory_change(fundamentals.get('cushing_draw_mbbl'))}; "
                f"seasonal {_scenario_label(seasonal.get('trigger') or fundamentals.get('scenario_trigger'))}"
            )
        else:
            lines.append(
                f"*{label}:* signal {fundamentals.get('supply_signal', 'available')}"
            )

    # Agreement
    state = agreement.get("state", "insufficient_data")
    conf = agreement.get("confidence", "low")
    streak = streaks.get("agreement_state_streak_days")
    streak_s = f", {streak}d streak" if streak else ""
    lines += ["", f"*Cross-market read:* {_state_label(state)} ({conf}{streak_s})"]

    scenario_board = _scenario_statuses(monitor)
    if scenario_board:
        lines.append(f"*Scenario board:* {scenario_board}")

    oi_line = _front_oi_line(oi)
    if oi_line:
        lines.append(f"*Options positioning:* {oi_line}")

    # Top catalyst
    top = (cats.get("top_events") or [])
    if top:
        ev = top[0]
        lines.append(f"*Top catalyst:* [{ev.get('score')}] "
                     f"{ev.get('direction') or 'unclear'} — {_short(ev.get('title'), 92)}")

    upcoming = _next_event(next_review)
    if upcoming:
        lines.append(f"*Next scheduled:* {upcoming}")

    return "\n".join(lines)


def _priority_alert_text(date_str: str, sections: dict, agreement: dict, eia_scenario: str) -> str:
    state = agreement.get("state", "")
    direction = "UPSIDE" if ("upside" in state or eia_scenario == "bull_confirmed") else "DOWNSIDE"
    mr = sections.get("market_risk", {})
    fut = (mr.get("futures", {}) or {})
    opt = (mr.get("options", {}) or {})
    term = sections.get("term_structure", {}) or {}
    history = sections.get("history_context", {}) or {}
    monitor = sections.get("monitor", {}) or {}
    scenarios = sections.get("scenarios", []) or []
    scorecard = sections.get("scorecard", {}) or {}
    cats = sections.get("catalysts", {}) or {}
    fundamentals = sections.get("fundamentals", sections.get("eia_fundamentals", {})) or {}
    code = fut.get("front_contract", "?")
    settle = fut.get("front_settlement")

    reasons = []
    if state in _ALERT_STATES:
        reasons.append(f"futures + options agree: {_state_label(state)} "
                       f"({agreement.get('confidence', 'low')})")
    if eia_scenario in _ALERT_SCENARIOS:
        reasons.append(f"{_fundamentals_label()} {_scenario_label(eia_scenario)}")
    for ev in (agreement.get("evidence") or [])[:3]:
        reasons.append(ev)

    scenario = "bull" if direction == "UPSIDE" else "bear"
    fired = _fired_trigger_descriptions(monitor, scenario)
    extremes = _extreme_context(history)
    scenario_level = _scenario_level(scenarios, direction)
    scorecard_line = _scorecard_line(scorecard, state)
    top = cats.get("top_events") or []

    lines = [
        f"🚨 *CurveLens Priority Alert — {direction} RISK*",
        f"_{date_str}_ · interrupt alert, separate from the routine daily brief",
        "",
        f"*Trigger:* {_state_label(state)} ({agreement.get('confidence', 'low')}) · "
        f"{_fundamentals_label()} {_scenario_label(eia_scenario)}",
        f"*Market now:* {code} {_price(settle)}; "
        f"M1-M2 {_price_change(fut.get('m1_m2_spread'), include_unit=True)}; "
        f"slope {_price_change(fut.get('curve_slope_per_month'))}/mo",
        f"*Options confirm:* ATM {_fmt_pct(opt.get('atm_iv'))}; "
        f"25Δ RR {_fmt_signed(opt.get('risk_reversal_25d'), '%', 100)}; "
        f"front/2nd IV {_fmt_pp(term.get('front_2nd_iv_spread'))}",
    ]
    if fundamentals.get("status") == "available":
        if fundamentals.get("crude_draw_mbbl") is not None:
            lines.append(
                f"*Fundamental backdrop:* crude "
                f"{_fmt_inventory_change(fundamentals.get('crude_draw_mbbl'))}; "
                f"Cushing {_fmt_inventory_change(fundamentals.get('cushing_draw_mbbl'))}; "
                f"supply {fundamentals.get('supply_signal', 'n/a')}"
            )
        else:
            lines.append(f"*Fundamental backdrop:* "
                         f"{fundamentals.get('supply_signal', 'available')}")
    if reasons:
        lines += ["", "*Why it fired:*"]
        lines += [f"- {reason}" for reason in reasons[:5]]
    if fired:
        lines.append("*Scenario triggers:*")
        lines += [f"- {_short(description, 110)}" for description in fired[:3]]
    if extremes or scenario_level or scorecard_line:
        lines += ["", "*Context / levels:*"]
        if extremes:
            lines.append(f"- Extremes: {'; '.join(extremes)}")
        if scenario_level:
            lines.append(f"- {scenario_level}")
        if scorecard_line:
            lines.append(f"- Prior {_state_label(state)} scorecard: {scorecard_line}")
    if top:
        event = top[0]
        lines.append(f"*Top catalyst:* [{event.get('score')}] "
                     f"{event.get('direction', 'unclear')} — {_short(event.get('title'), 92)}")
    lines += ["", "Use the daily brief for the full scenario table, positioning, and next review."]
    return "\n".join(lines)


# ── Commands ───────────────────────────────────────────────────────────────

def queue_message(msg_type: str, date_str: str, text: str) -> dict:
    """Queue one message with the standard <date>:<type> dedup id (D1).

    Used by agent/event_run.py for event-driven messages (EIA_FLASH,
    COT_UPDATE). Same guarantees as --prepare: a given date can queue each
    type at most once; already-delivered ids are never re-queued.
    """
    msg_id = f"{date_str}:{msg_type}"
    pending = _load(PENDING_PATH)
    delivered_ids = {d["id"] for d in _load(DELIVERED_PATH)}
    if msg_id in delivered_ids or msg_id in {p["id"] for p in pending}:
        return {"result": "SKIPPED", "id": msg_id, "reason": "already queued or delivered"}
    pending.append({
        "id": msg_id, "type": msg_type, "date": date_str,
        "text": text, "queued_at": _now_iso(),
    })
    _save(PENDING_PATH, pending)
    return {"result": "QUEUED", "id": msg_id, "pending_total": len(pending)}


def cmd_prepare(date_str: str) -> None:
    report_json = DATA_DIR / "reports" / f"{date_str}.json"
    if not report_json.exists():
        _emit({"result": "NO_REPORT", "date": date_str,
               "detail": f"{report_json} not found — run run_pipeline.py first"})
        sys.exit(1)

    report = json.loads(report_json.read_text())
    sections = report.get("sections", {})

    agr_path = DATA_DIR / "gold" / "agreement" / f"trade_date={date_str}" / "agreement.json"
    agreement = json.loads(agr_path.read_text()) if agr_path.exists() else {}

    eia = sections.get("fundamentals", sections.get("eia_fundamentals", {})) or {}
    eia_scenario = eia.get("scenario_trigger", "none")
    state = agreement.get("state", "insufficient_data")
    alert_worthy = (state in _ALERT_STATES) or (eia_scenario in _ALERT_SCENARIOS)

    pending = _load(PENDING_PATH)
    delivered_ids = {d["id"] for d in _load(DELIVERED_PATH)}
    pending_ids = {p["id"] for p in pending}

    to_queue = [("DAILY_BRIEF", _daily_brief_text(date_str, sections, agreement))]
    if alert_worthy:
        to_queue.append(
            ("PRIORITY_ALERT", _priority_alert_text(date_str, sections, agreement, eia_scenario))
        )

    queued, skipped = [], []
    for msg_type, text in to_queue:
        msg_id = f"{date_str}:{msg_type}"
        if msg_id in delivered_ids or msg_id in pending_ids:
            skipped.append(msg_id)
            continue
        pending.append({
            "id": msg_id,
            "type": msg_type,
            "date": date_str,
            "text": text,
            "queued_at": _now_iso(),
        })
        queued.append(msg_id)

    _save(PENDING_PATH, pending)
    _emit({
        "result": "PREPARED",
        "date": date_str,
        "alert_worthy": alert_worthy,
        "queued": queued,
        "skipped_already_seen": skipped,
        "pending_total": len(pending),
    })


def cmd_list_pending() -> None:
    pending = _load(PENDING_PATH)
    _emit({
        "result": "PENDING",
        "count": len(pending),
        "instructions": (
            "Deliver each item's `text` verbatim via your Telegram integration "
            "(Markdown parse mode). Send PRIORITY_ALERT items immediately; a "
            "DAILY_BRIEF is the routine digest. After each successful send, ack "
            "its id with: python agent/notify.py --ack <id>"
        ),
        "items": pending,
    })


def cmd_is_new(date_str: str) -> None:
    """Report whether a bulletin date still needs processing.

    A date is "new" (needs the pipeline + delivery) when its DAILY_BRIEF has not
    yet been delivered. Used as the up-front freshness gate: the agent downloads
    the CME "current" bulletin, reads its internal date, and calls this before
    saving/recomputing — if not new, it discards the download and stays silent.

    Delivered gates out; merely-pending does not — so a run that crashed after
    queueing but before delivering still counts as new and can recover.
    """
    delivered_ids = {d["id"] for d in _load(DELIVERED_PATH)}
    already_delivered = f"{date_str}:DAILY_BRIEF" in delivered_ids
    _emit({
        "result": "DATE_STATUS",
        "date": date_str,
        "is_new": not already_delivered,
        "already_delivered": already_delivered,
    })


def cmd_ack(ids: list[str], ack_all: bool) -> None:
    pending = _load(PENDING_PATH)
    delivered = _load(DELIVERED_PATH)

    if ack_all:
        ack_set = {p["id"] for p in pending}
    else:
        ack_set = set(ids)

    still_pending, acked = [], []
    for p in pending:
        if p["id"] in ack_set:
            delivered.append({**p, "delivered_at": _now_iso()})
            acked.append(p["id"])
        else:
            still_pending.append(p)

    _save(PENDING_PATH, still_pending)
    _save(DELIVERED_PATH, delivered)
    _emit({"result": "ACKED", "acked": acked, "still_pending": len(still_pending)})


def main() -> None:
    parser = argparse.ArgumentParser(description="CurveLens alert prep + delivery queue")
    parser.add_argument("--prepare", action="store_true", help="Queue messages for a date")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (required with --prepare)")
    parser.add_argument("--is-new", metavar="DATE",
                        help="Report whether DATE still needs processing (freshness gate)")
    parser.add_argument("--list-pending", action="store_true", help="Print queued messages as JSON")
    parser.add_argument("--ack", nargs="+", metavar="ID", help="Mark message id(s) delivered")
    parser.add_argument("--ack-all", action="store_true", help="Mark all pending messages delivered")
    args = parser.parse_args()

    if args.prepare:
        if not args.date:
            _emit({"result": "ERROR", "detail": "--prepare requires --date"})
            sys.exit(1)
        cmd_prepare(args.date)
    elif args.is_new:
        cmd_is_new(args.is_new)
    elif args.list_pending:
        cmd_list_pending()
    elif args.ack or args.ack_all:
        cmd_ack(args.ack or [], args.ack_all)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

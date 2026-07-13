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


def _daily_brief_text(date_str: str, sections: dict, agreement: dict) -> str:
    mr = sections.get("market_risk", {})
    fut = mr.get("futures", {}) or {}
    opt = mr.get("options", {}) or {}
    eia = sections.get("eia_fundamentals", {}) or {}
    cats = sections.get("catalysts", {}) or {}

    lines = [f"📈 *CurveLens Daily Brief — {date_str}*", ""]

    # Futures line
    if fut:
        code = fut.get("front_contract", "?")
        settle = fut.get("front_settlement")
        ret = fut.get("front_return_1d")
        struct = "contango" if fut.get("contango") else "backwardation"
        settle_s = f"${settle:.2f}" if settle is not None else "n/a"
        lines.append(f"*Front:* {code} {settle_s} ({_fmt_signed(ret, '%', 100)} 1d) — {struct}")

    # Options line
    if opt:
        atm = opt.get("atm_iv")
        rr = opt.get("risk_reversal_25d")
        atm_s = f"{atm*100:.1f}%" if atm is not None else "n/a"
        lines.append(f"*Vol:* ATM {atm_s} | 25Δ RR {_fmt_signed(rr, '%', 100)}")

    # EIA line
    if eia and eia.get("status") == "available":
        draw = eia.get("crude_draw_mbbl")
        # crude_draw > 0 means stocks fell (bullish); display with correct sign
        draw_s = f"{-draw:+,.0f} MBBL" if draw is not None else "n/a"
        lines.append(
            f"*EIA:* crude {draw_s} | supply {eia.get('supply_signal','n/a')} "
            f"| Cushing {eia.get('cushing_signal','n/a')}"
        )

    # Agreement
    state = agreement.get("state", "insufficient_data")
    conf = agreement.get("confidence", "low")
    lines += ["", f"*Cross-market read:* {state} ({conf})"]

    # Top catalyst
    top = (cats.get("top_events") or [])
    if top:
        ev = top[0]
        lines.append(f"*Top catalyst:* [{ev.get('score')}] {ev.get('title','')[:80]}")

    return "\n".join(lines)


def _priority_alert_text(date_str: str, sections: dict, agreement: dict, eia_scenario: str) -> str:
    state = agreement.get("state", "")
    direction = "UPSIDE" if ("upside" in state or eia_scenario == "bull_confirmed") else "DOWNSIDE"
    mr = sections.get("market_risk", {})
    fut = (mr.get("futures", {}) or {})
    code = fut.get("front_contract", "?")
    settle = fut.get("front_settlement")
    settle_s = f"${settle:.2f}/bbl" if settle is not None else ""

    reasons = []
    if state in _ALERT_STATES:
        reasons.append(f"futures + options agree ({agreement.get('confidence','')})")
    if eia_scenario in _ALERT_SCENARIOS:
        reasons.append(f"EIA {eia_scenario}")
    for ev in (agreement.get("evidence") or [])[:3]:
        reasons.append(ev)

    lines = [
        f"🚨 *CurveLens PRIORITY — {direction} RISK*",
        f"_{date_str}_ · {code} {settle_s}",
        "",
        f"*State:* {state}",
    ]
    if reasons:
        lines.append("*Why:*")
        lines += [f"  • {r}" for r in reasons]
    lines += ["", "See the full daily brief for scenarios and levels."]
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

    eia = sections.get("eia_fundamentals", {}) or {}
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

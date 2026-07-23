#!/usr/bin/env python
"""Durable controller for native Codex sub-agent analysis orchestration.

The controller never invokes a model. It emits tasks for the host Codex agent,
validates returned JSON, enforces phase order, and runs deterministic preparation
or finalization commands.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).parent.parent
CCVM_DIR = REPO_ROOT / "ccvm"
sys.path.insert(0, str(CCVM_DIR / "src"))

from ccvm.reference.product import get_product
from ccvm.runtime import data_dir
from ccvm.workflow.finalize import AnalysisValidationError, validate_and_render
from ccvm.workflow.monitoring import build_monitor, monitor_paths, record_event
from ccvm.workflow.orchestration import (
    advance_state, initialize_state, load_state, next_actions,
    refresh_after_remediation, save_state,
)


def _emit(value: dict, ok: bool = True) -> None:
    print(json.dumps(value))
    raise SystemExit(0 if ok else 1)


@contextmanager
def _run_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AnalysisValidationError("another operator is advancing this run") from exc
        yield


def _prepare(as_of: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "agent" / "run_analysis_workflow.py"),
         "--date", as_of],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    try:
        result = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AnalysisValidationError(
            f"preparation returned non-JSON output (exit {proc.returncode})"
        ) from exc
    if result.get("result") not in {
        "ANALYSIS_PACKETS_READY", "ANALYSIS_PACKETS_READY_WITH_LIMITATIONS",
    }:
        result["preparation_exit_code"] = proc.returncode
        return result
    return result


def _summary(state: dict) -> dict:
    state_path = Path(state["manifest_path"]).parent / "run.json"
    result = {
        "result": "ORCHESTRATION_COMPLETE" if state["phase"] == "COMPLETE" else (
            "ORCHESTRATION_BLOCKED" if state["phase"] == "BLOCKED" else "ORCHESTRATION_ACTIVE"
        ),
        "run_id": state["run_id"], "product": state["product"],
        "date": state["trade_date"], "phase": state["phase"],
        "state_path": str(state_path),
        "actions": next_actions(state), "workflow_mode": "agent_orchestrated",
        "delivery_queued": False,
    }
    try:
        build_monitor(state_path)
        events_path, monitor_json, monitor_md = monitor_paths(state)
        result.update({
            "monitor_json": str(monitor_json), "monitor_md": str(monitor_md),
            "monitor_events": str(events_path),
        })
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result["monitor_error"] = str(exc)
    if state.get("block_reason"):
        result["detail"] = state["block_reason"]
    if state["phase"] == "COMPLETE":
        result["analysis_json"] = state.get("analysis_json")
        result["analysis_md"] = state.get("analysis_md")
        result["statistics_md"] = state.get("statistics_md")
    if state["phase"] == "QC_REVIEW_REQUIRED" and state["qc"].get("last_error"):
        result["validation_error"] = state["qc"]["last_error"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "advance", "status", "inspect"))
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today ET)")
    parser.add_argument("--restart", action="store_true",
                        help="Discard orchestration state for this date and prepare anew")
    parser.add_argument("--max-agent-corrections", type=int, default=2)
    args = parser.parse_args()
    try:
        as_of = date.fromisoformat(args.date) if args.date else datetime.now(
            ZoneInfo("America/New_York")
        ).date()
    except ValueError:
        _emit({"result": "ERROR", "detail": "invalid date"}, False)
    as_of_str = as_of.isoformat()
    run_dir = data_dir() / "analysis_workflow" / f"trade_date={as_of_str}"
    state_path = run_dir / "run.json"
    try:
        with _run_lock(run_dir / "run.lock"):
            if args.command in {"status", "inspect"}:
                _emit(_summary(load_state(state_path)))

            if args.command == "start":
                if args.restart:
                    state_path.unlink(missing_ok=True)
                if state_path.exists():
                    _emit(_summary(load_state(state_path)))
                prepared = _prepare(as_of_str)
                if prepared.get("result") not in {
                    "ANALYSIS_PACKETS_READY", "ANALYSIS_PACKETS_READY_WITH_LIMITATIONS",
                }:
                    _emit(prepared, prepared.get("result") == "NEED_CME_PDF")
                state_path, state = initialize_state(
                    manifest_path=Path(prepared["manifest"]),
                    quality=prepared["quality_report"],
                    quality_attempts=prepared["quality_attempts"],
                    repo_root=REPO_ROOT,
                    max_qc_reviews=get_product().analysis_max_quality_attempts,
                    max_agent_corrections=args.max_agent_corrections,
                )
                _emit(_summary(state))

            state = load_state(state_path)
            if state["phase"] == "REMEDIATION_REQUIRED":
                # The only current recipe is a complete deterministic market
                # recollection/re-normalization/recompute. The allowlist is
                # checked before this phase can be entered.
                prepared = _prepare(as_of_str)
                if prepared.get("result") not in {
                    "ANALYSIS_PACKETS_READY", "ANALYSIS_PACKETS_READY_WITH_LIMITATIONS",
                }:
                    _emit(prepared, False)
                state = refresh_after_remediation(
                    state_path, manifest_path=Path(prepared["manifest"]),
                    quality=prepared["quality_report"],
                    quality_attempts=prepared["quality_attempts"], repo_root=REPO_ROOT,
                )
            elif state["phase"] not in {"COMPLETE", "BLOCKED"}:
                state = advance_state(state_path, REPO_ROOT)

            if state["phase"] == "READY_TO_FINALIZE":
                previous_phase = state["phase"]
                output_dir = data_dir() / "analysis" / f"trade_date={as_of_str}"
                json_path, md_path, statistics_path = validate_and_render(
                    Path(state["manifest_path"]), output_dir,
                )
                state["phase"] = "COMPLETE"
                state["analysis_json"] = str(json_path)
                state["analysis_md"] = str(md_path)
                state["statistics_md"] = str(statistics_path)
                record_event(
                    state, "phase_changed", actor="controller",
                    detail=f"{previous_phase} -> {state['phase']}",
                    from_phase=previous_phase, to_phase=state["phase"],
                )
                record_event(
                    state, "run_finalized", actor="controller",
                    detail="Validated and rendered integrated analysis and statistics outputs.",
                    analysis_json=str(json_path), analysis_md=str(md_path),
                    statistics_md=str(statistics_path),
                )
                save_state(state_path, state)
            _emit(_summary(state), state["phase"] != "BLOCKED")
    except (AnalysisValidationError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        _emit({"result": "ORCHESTRATION_ERROR", "date": as_of_str, "detail": str(exc)}, False)


if __name__ == "__main__":
    main()

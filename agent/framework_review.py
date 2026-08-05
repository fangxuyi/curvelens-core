#!/usr/bin/env python
"""Durable controller for advisory-only, repository-wide framework review."""
from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).parent.parent
CCVM_DIR = REPO_ROOT / "ccvm"
sys.path.insert(0, str(CCVM_DIR / "src"))

from ccvm.learning.framework_review import write_framework_review_packet
from ccvm.workflow.finalize import AnalysisValidationError
from ccvm.workflow.framework_review import (
    finalize_framework_review, framework_review_response_template,
    review_signal_ids, write_framework_review_task,
)

MAX_CORRECTIONS = 2


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AnalysisValidationError("another operator is advancing framework review") from exc
        yield


def _paths(as_of: str) -> dict[str, Path]:
    root = CCVM_DIR / "data" / "framework_learning" / f"review_as_of={as_of}"
    return {
        "root": root,
        "state": root / "run.json",
        "packet": root / "framework_review.packet.json",
        "template": root / "framework_review.template.json",
        "task": root / "framework_review.task.md",
        "response": root / "framework_review.response.json",
        "suggestions_json": root / "framework_suggestions.json",
        "suggestions_md": root / "framework_suggestions.md",
    }


def _action(paths: dict[str, Path]) -> dict:
    return {
        "type": "RUN_FRAMEWORK_REVIEWER",
        "agent_type": "curvelens_framework_reviewer",
        "task_path": str(paths["task"]),
        "response_path": str(paths["response"]),
    }


def _summary(state: dict, paths: dict[str, Path]) -> dict:
    phase = state["phase"]
    result = {
        "result": (
            "FRAMEWORK_REVIEW_COMPLETE" if phase == "COMPLETE" else
            "FRAMEWORK_REVIEW_BLOCKED" if phase == "BLOCKED" else
            "FRAMEWORK_REVIEW_REQUIRED"
        ),
        "date": state["as_of"],
        "phase": phase,
        "products": state["products"],
        "eligible_signal_count": state["eligible_signal_count"],
        "corrections": state["corrections"],
        "packet": str(paths["packet"]),
        "task": str(paths["task"]),
        "actions": [_action(paths)] if phase == "REVIEW_REQUIRED" else [],
    }
    if state.get("last_error"):
        result["detail"] = state["last_error"]
    if phase == "COMPLETE":
        result.update({
            "suggestion_count": state["suggestion_count"],
            "suggestions_json": str(paths["suggestions_json"]),
            "suggestions_md": str(paths["suggestions_md"]),
        })
    return result


def _start(as_of: date, paths: dict[str, Path]) -> dict:
    packet = write_framework_review_packet(
        CCVM_DIR / "data" / "products", paths["packet"], as_of=as_of,
    )
    template = framework_review_response_template(packet)
    _write_json(paths["template"], template)
    write_framework_review_task(
        paths["packet"], paths["template"], paths["response"], paths["task"],
    )
    eligible = review_signal_ids(packet)
    state = {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "phase": "REVIEW_REQUIRED" if eligible else "COMPLETE",
        "packet_id": packet["packet_id"],
        "products": packet["products_scanned"],
        "eligible_signal_count": len(eligible),
        "corrections": 0,
        "last_error": "",
        "suggestion_count": 0,
    }
    if not eligible:
        empty_response = {
            **template,
            "status": "complete",
            "executive_summary": "No framework-eligible signals were available.",
            "boundary_assessment": "All observed learning remains product-local or insufficient.",
        }
        _write_json(paths["response"], empty_response)
        result = finalize_framework_review(
            paths["packet"], paths["response"], paths["suggestions_json"],
            paths["suggestions_md"],
        )
        state["suggestion_count"] = result["suggestion_count"]
    _write_json(paths["state"], state)
    return state


def _advance(state: dict, paths: dict[str, Path]) -> dict:
    if state["phase"] != "REVIEW_REQUIRED" or not paths["response"].exists():
        return state
    try:
        result = finalize_framework_review(
            paths["packet"], paths["response"], paths["suggestions_json"],
            paths["suggestions_md"],
        )
    except AnalysisValidationError as exc:
        state["corrections"] += 1
        state["last_error"] = str(exc)
        archive = paths["response"].with_name(
            f"framework_review.response.invalid-attempt-{state['corrections']}.json"
        )
        paths["response"].replace(archive)
        if state["corrections"] >= MAX_CORRECTIONS:
            state["phase"] = "BLOCKED"
        else:
            write_framework_review_task(
                paths["packet"], paths["template"], paths["response"], paths["task"],
                validation_error=state["last_error"],
            )
        _write_json(paths["state"], state)
        return state
    state["phase"] = "COMPLETE"
    state["last_error"] = ""
    state["suggestion_count"] = result["suggestion_count"]
    _write_json(paths["state"], state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "advance", "status"))
    parser.add_argument("--date", help="Review cutoff YYYY-MM-DD (default: today ET)")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    try:
        as_of = date.fromisoformat(args.date) if args.date else datetime.now(
            ZoneInfo("America/New_York")
        ).date()
    except ValueError:
        print(json.dumps({"result": "ERROR", "detail": "invalid date"}))
        raise SystemExit(1)
    paths = _paths(as_of.isoformat())
    try:
        with _lock(CCVM_DIR / "data" / "framework_learning" / "review.lock"):
            if args.command == "start" and args.restart:
                shutil.rmtree(paths["root"], ignore_errors=True)
            if not paths["state"].exists():
                if args.command != "start":
                    raise AnalysisValidationError("framework review has not been started")
                state = _start(as_of, paths)
            else:
                state = json.loads(paths["state"].read_text())
            if args.command == "advance":
                state = _advance(state, paths)
            result = _summary(state, paths)
            print(json.dumps(result))
            raise SystemExit(1 if state["phase"] == "BLOCKED" else 0)
    except (AnalysisValidationError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({
            "result": "FRAMEWORK_REVIEW_ERROR", "date": as_of.isoformat(),
            "detail": str(exc),
        }))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

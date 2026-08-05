"""Post-run outcome materialization and bounded retrospective review."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ccvm.analytics.outcomes import persist_outcome, realize_outcome
from ccvm.schemas.learning import (
    EvaluationRecord, InvestigatorEvaluationRecord, OutcomeRecord,
)
from ccvm.workflow.mobile_evaluation import (
    MOBILE_RELEVANCE_EVALUATOR_VERSION,
    aggregate_mobile_relevance,
    evaluate_mobile_selection,
)

from .evaluation import EVALUATOR_VERSION, aggregate_evaluations, evaluate_forecast
from .finalize import AnalysisValidationError
from .investigator_evaluation import (
    INVESTIGATOR_EVALUATOR_VERSION,
    aggregate_investigator_evaluations,
    evaluate_investigator_findings,
)

RETROSPECTIVE_SCHEMA_VERSION = 3


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes()) if path.exists() else ""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, indent=2, default=str))
    temp.replace(path)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise AnalysisValidationError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise AnalysisValidationError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AnalysisValidationError(f"{label} must be a JSON object")
    return value


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events[-500:]


def _trace_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    actors: dict[str, int] = {}
    for item in events:
        event = str(item.get("event", "unknown"))
        actor = str(item.get("actor", "controller"))
        counts[event] = counts.get(event, 0) + 1
        actors[actor] = actors.get(actor, 0) + 1
    return {
        "event_count": len(events),
        "event_counts": counts,
        "actor_event_counts": actors,
        "validation_rejections": counts.get("validation_rejected", 0),
        "agent_redispatches": counts.get("agent_redispatched", 0),
        "scope_note": (
            "Only controller-visible tasks, outputs, validation events, and phase transitions "
            "are evaluated; hidden chain-of-thought is not collected."
        ),
    }


def _response_template(
    packet_id: str, evaluations: list[EvaluationRecord],
    mobile_evaluations: list[dict[str, Any]],
    investigator_evaluations: list[InvestigatorEvaluationRecord],
) -> dict[str, Any]:
    return {
        "packet_id": packet_id,
        "status": "complete|limited",
        "outcome_assessment": "",
        "trace_assessment": "",
        "priority_assessment": "",
        "mobile_assessment": "",
        "investigator_assessment": "",
        "forecast_reviews": [{
            "forecast_id": item.forecast_id,
            "assessment": "correct|incorrect",
            "diagnosis": "",
            "improvement": "",
        } for item in evaluations if item.status == "scored"],
        "mobile_reviews": [{
            "source_view_rank": item["source_view_rank"],
            "assessment": "appropriate|missed_material|false_prominence",
            "diagnosis": "",
            "improvement": "",
        } for item in mobile_evaluations if item["status"] == "scored"],
        "investigator_reviews": [{
            "finding_id": item.finding_id,
            "assessment": "materiality_matched|overstated|understated",
            "lead_disposition": item.lead_disposition,
            "diagnosis": "",
            "improvement": "",
        } for item in investigator_evaluations if item.status == "scored"],
        "candidate_advisories": [],
    }


def validate_retrospective_response(
    packet_path: Path, response_path: Path,
) -> dict[str, Any]:
    packet = _load_object(packet_path, "retrospective packet")
    response = _load_object(response_path, "retrospective response")
    if response.get("packet_id") != packet.get("packet_id"):
        raise AnalysisValidationError("retrospective response does not match this packet")
    if response.get("status") not in {"complete", "limited"}:
        raise AnalysisValidationError("retrospective response has invalid status")
    for field in (
        "outcome_assessment", "trace_assessment", "priority_assessment",
        "mobile_assessment", "investigator_assessment",
    ):
        if not str(response.get(field, "")).strip():
            raise AnalysisValidationError(f"retrospective response requires {field}")

    scored = {
        item["forecast_id"]: item
        for item in packet["evaluations"] if item["status"] == "scored"
    }
    reviews = response.get("forecast_reviews")
    if not isinstance(reviews, list) or {
        item.get("forecast_id") for item in reviews if isinstance(item, dict)
    } != set(scored):
        raise AnalysisValidationError("retrospective must review every scored forecast exactly once")
    if len(reviews) != len(scored):
        raise AnalysisValidationError("retrospective has duplicate forecast reviews")
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise AnalysisValidationError(f"forecast_reviews[{index}] must be an object")
        expected = "correct" if scored[review["forecast_id"]]["hit"] else "incorrect"
        if review.get("assessment") != expected:
            raise AnalysisValidationError(
                f"forecast_reviews[{index}].assessment must preserve deterministic scoring"
            )
        for field in ("diagnosis", "improvement"):
            if not str(review.get(field, "")).strip():
                raise AnalysisValidationError(f"forecast_reviews[{index}] requires {field}")

    mobile_scored = {
        item["source_view_rank"]: item
        for item in packet.get("mobile_relevance", {}).get("records", [])
        if item.get("status") == "scored"
    }
    mobile_reviews = response.get("mobile_reviews")
    if not isinstance(mobile_reviews, list) or {
        item.get("source_view_rank") for item in mobile_reviews if isinstance(item, dict)
    } != set(mobile_scored):
        raise AnalysisValidationError(
            "retrospective must review every scored mobile candidate exactly once"
        )
    if len(mobile_reviews) != len(mobile_scored):
        raise AnalysisValidationError("retrospective has duplicate mobile candidate reviews")
    for index, review in enumerate(mobile_reviews):
        if not isinstance(review, dict):
            raise AnalysisValidationError(f"mobile_reviews[{index}] must be an object")
        scored_item = mobile_scored[review["source_view_rank"]]
        expected = "appropriate"
        if scored_item["missed_material"]:
            expected = "missed_material"
        elif scored_item["false_prominence"]:
            expected = "false_prominence"
        if review.get("assessment") != expected:
            raise AnalysisValidationError(
                f"mobile_reviews[{index}].assessment must preserve deterministic relevance scoring"
            )
        for field in ("diagnosis", "improvement"):
            if not str(review.get(field, "")).strip():
                raise AnalysisValidationError(f"mobile_reviews[{index}] requires {field}")

    investigator_scored = {
        item["finding_id"]: item
        for item in (packet.get("investigator_relevance") or {}).get("records", [])
        if item.get("status") == "scored"
    }
    investigator_reviews = response.get("investigator_reviews")
    if not isinstance(investigator_reviews, list) or {
        item.get("finding_id") for item in investigator_reviews if isinstance(item, dict)
    } != set(investigator_scored) or len(investigator_reviews) != len(investigator_scored):
        raise AnalysisValidationError(
            "retrospective must review every scored investigator finding exactly once"
        )
    expected_scores = {"low": 0, "medium": 1, "high": 2}
    for index, review in enumerate(investigator_reviews):
        if not isinstance(review, dict):
            raise AnalysisValidationError(f"investigator_reviews[{index}] must be an object")
        scored_item = investigator_scored[review["finding_id"]]
        expected_score = expected_scores[scored_item["expected_materiality"]]
        actual_score = scored_item["materiality_score"]
        expected_assessment = "materiality_matched" if actual_score == expected_score else (
            "understated" if actual_score > expected_score else "overstated"
        )
        if review.get("assessment") != expected_assessment \
                or review.get("lead_disposition") != scored_item["lead_disposition"]:
            raise AnalysisValidationError(
                f"investigator_reviews[{index}] must preserve deterministic attribution"
            )
        for field in ("diagnosis", "improvement"):
            if not str(review.get(field, "")).strip():
                raise AnalysisValidationError(f"investigator_reviews[{index}] requires {field}")

    advisories = response.get("candidate_advisories")
    if not isinstance(advisories, list) or len(advisories) > 8:
        raise AnalysisValidationError("candidate_advisories must be a list of at most 8 items")
    seen: set[str] = set()
    for index, advisory in enumerate(advisories):
        if not isinstance(advisory, dict):
            raise AnalysisValidationError(f"candidate_advisories[{index}] must be an object")
        key = advisory.get("advisory_key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            raise AnalysisValidationError(f"candidate_advisories[{index}] has unsafe advisory_key")
        if key in seen:
            raise AnalysisValidationError("candidate_advisories has duplicate advisory_key values")
        seen.add(key)
        support = advisory.get("supporting_forecast_ids")
        if not isinstance(support, list) or not support or set(support) - set(scored):
            raise AnalysisValidationError(
                f"candidate_advisories[{index}] must cite scored forecasts from this packet"
            )
        scope = advisory.get("scope")
        if not isinstance(scope, dict) or set(scope) - {
            "dimension", "horizon_sessions", "confidence", "source_view_rank",
        }:
            raise AnalysisValidationError(f"candidate_advisories[{index}] has invalid scope")
        for field in ("observation", "suggested_adjustment"):
            if not str(advisory.get(field, "")).strip():
                raise AnalysisValidationError(f"candidate_advisories[{index}] requires {field}")
    return response


def prepare_retrospective(
    data_root: Path, trade_date: str, *, generated_at: datetime | None = None,
) -> dict[str, Any]:
    try:
        source_date = date.fromisoformat(trade_date)
    except ValueError as exc:
        raise AnalysisValidationError("retrospective trade date is invalid") from exc
    now = generated_at or datetime.now(timezone.utc)
    analysis_path = data_root / "analysis" / f"trade_date={trade_date}" / "analysis.json"
    analysis = _load_object(analysis_path, "analysis")
    if analysis.get("trade_date") != trade_date:
        raise AnalysisValidationError("analysis trade date does not match retrospective date")
    contract = analysis.get("forecast_contract")
    forecasts = (analysis.get("synthesis") or {}).get("forecast_ledger")
    if not isinstance(contract, dict) or not isinstance(forecasts, list):
        raise AnalysisValidationError("analysis lacks a forecast contract or ledger")

    run_dir = data_root / "learning" / "evaluations" / f"trade_date={trade_date}"
    outcomes_dir = run_dir / "outcomes"
    analysis_hash = _hash_file(analysis_path)
    outcome_models: list[OutcomeRecord] = []
    outcome_hashes: dict[str, str] = {}
    for forecast in forecasts:
        outcome = realize_outcome(
            forecast, contract, source_date, data_root / "reports",
            analysis_path=analysis_path, generated_at=now,
        )
        name = hashlib.sha256(outcome.forecast_id.encode()).hexdigest()[:20]
        path = outcomes_dir / f"{name}.json"
        outcome = persist_outcome(outcome, path)
        outcome_models.append(outcome)
        outcome_hashes[outcome.forecast_id] = _hash_file(path)

    evaluations = [
        evaluate_forecast(
            forecast, outcome, analysis_sha256=analysis_hash,
            outcome_sha256=outcome_hashes[outcome.forecast_id], evaluated_at=now,
        )
        for forecast, outcome in zip(forecasts, outcome_models, strict=True)
    ]
    mobile_contract = analysis.get("mobile_relevance_contract")
    mobile_selection = (analysis.get("synthesis") or {}).get("mobile_selection")
    mobile_records = []
    mobile_unavailable_reason = ""
    if isinstance(mobile_contract, dict) and isinstance(mobile_selection, dict) \
            and mobile_selection.get("candidates"):
        mobile_records = evaluate_mobile_selection(
            mobile_selection, forecasts, outcome_models, contract=mobile_contract,
            analysis_sha256=analysis_hash, outcome_hashes=outcome_hashes,
            evaluated_at=now,
        )
    else:
        mobile_unavailable_reason = "analysis lacks a mobile selection or relevance contract"
    mobile_payload = {
        "evaluator_version": MOBILE_RELEVANCE_EVALUATOR_VERSION,
        "status": "available" if mobile_records else "unavailable",
        "unavailable_reason": mobile_unavailable_reason,
        "records": [item.model_dump(mode="json") for item in mobile_records],
        "aggregate": aggregate_mobile_relevance(mobile_records),
    }
    investigator_contract = analysis.get("investigator_relevance_contract")
    investigator_analyses = analysis.get("investigator_analyses")
    investigator_feedback = (analysis.get("synthesis") or {}).get("investigator_feedback")
    investigator_records: list[InvestigatorEvaluationRecord] = []
    investigator_unavailable_reason = ""
    if isinstance(investigator_contract, dict) \
            and isinstance(investigator_analyses, dict) \
            and isinstance(investigator_feedback, list):
        investigator_records = evaluate_investigator_findings(
            investigator_analyses, investigator_feedback,
            contract=investigator_contract, source_date=source_date,
            reports_dir=data_root / "reports",
            outcomes_dir=run_dir / "investigator_outcomes",
            analysis_path=analysis_path, analysis_sha256=analysis_hash,
            evaluated_at=now,
        )
    else:
        investigator_unavailable_reason = (
            "analysis lacks investigator findings, feedback, or a relevance contract"
        )
    investigator_payload = {
        "evaluator_version": INVESTIGATOR_EVALUATOR_VERSION,
        "status": "available" if not investigator_unavailable_reason else "unavailable",
        "unavailable_reason": investigator_unavailable_reason,
        "records": [item.model_dump(mode="json") for item in investigator_records],
        "aggregate": aggregate_investigator_evaluations(investigator_records),
    }
    evaluation_artifact = {
        "schema_version": RETROSPECTIVE_SCHEMA_VERSION,
        "trade_date": trade_date,
        "generated_at": now.isoformat(),
        "evaluations": [item.model_dump(mode="json") for item in evaluations],
        "aggregate": aggregate_evaluations(evaluations),
        "mobile_relevance": mobile_payload,
        "investigator_relevance": investigator_payload,
    }
    evaluation_path = run_dir / "evaluation.json"
    _write_json(evaluation_path, evaluation_artifact)

    events_path = data_root / "analysis_workflow" / f"trade_date={trade_date}" / "workflow_events.jsonl"
    events = _load_events(events_path)
    identity = {
        "analysis_sha256": analysis_hash,
        "outcome_hashes": outcome_hashes,
        "events_sha256": _hash_file(events_path),
        "evaluator_version": EVALUATOR_VERSION,
        "mobile_relevance_evaluator_version": MOBILE_RELEVANCE_EVALUATOR_VERSION,
        "investigator_evaluator_version": INVESTIGATOR_EVALUATOR_VERSION,
    }
    packet_id = _hash_bytes(json.dumps(identity, sort_keys=True).encode())
    packet = {
        "schema_version": RETROSPECTIVE_SCHEMA_VERSION,
        "packet_id": packet_id,
        "product": analysis.get("product"),
        "trade_date": trade_date,
        "generated_at": now.isoformat(),
        "source_artifacts": {
            "analysis_path": str(analysis_path), "analysis_sha256": analysis_hash,
            "workflow_events_path": str(events_path),
            "workflow_events_sha256": identity["events_sha256"],
        },
        "top_views": (analysis.get("synthesis") or {}).get("top_views", []),
        "forecasts": forecasts,
        "outcomes": [item.model_dump(mode="json") for item in outcome_models],
        "evaluations": evaluation_artifact["evaluations"],
        "aggregate": evaluation_artifact["aggregate"],
        "mobile_relevance": mobile_payload,
        "investigator_relevance": investigator_payload,
        "trace_summary": _trace_summary(events),
        "review_contract": {
            "language_rule": (
                "Describe evidence as associated with outcomes. Do not infer causation from timing, "
                "correlation, or forecast error."
            ),
            "candidate_rule": (
                "Propose only bounded candidates supported by scored forecasts. Candidates are not active memory."
            ),
        },
    }
    packet_path = run_dir / "retrospective.packet.json"
    old_packet_id = None
    if packet_path.exists():
        try:
            old_packet_id = json.loads(packet_path.read_text()).get("packet_id")
        except json.JSONDecodeError:
            pass
    _write_json(packet_path, packet)
    template_path = run_dir / "retrospective.template.json"
    response_path = run_dir / "retrospective.response.json"
    task_path = run_dir / "retrospective.task.md"
    scored = [item for item in evaluations if item.status == "scored"]
    _write_json(
        template_path,
        _response_template(
            packet_id, evaluations, mobile_payload["records"], investigator_records,
        ),
    )
    task_path.write_text(
        "# CurveLens retrospective review\n\n"
        "Review only the controller-visible packet and deterministic outcomes. Do not spawn agents. "
        "Do not reinterpret outcome labels or claim causation. Diagnose prioritization, calibration, "
        "mobile false prominence, missed material information, investigator finding materiality, "
        "lead use and rejected-but-material findings, "
        "evidence use, and trace friction; write the completed JSON only to "
        f"`{response_path}` using `{template_path}`. Candidate advisories remain inactive hypotheses.\n"
    )
    if old_packet_id != packet_id:
        response_path.unlink(missing_ok=True)

    base = {
        "product": analysis.get("product"), "date": trade_date,
        "packet": str(packet_path), "evaluation": str(evaluation_path),
        "outcomes_dir": str(outcomes_dir),
    }
    if not scored:
        return {"result": "RETROSPECTIVE_PENDING", **base, "actions": []}
    if not response_path.exists():
        return {
            "result": "RETROSPECTIVE_REQUIRED", **base,
            "actions": [{
                "action": "RUN_RETROSPECTIVE",
                "agent_type": "curvelens_retrospective",
                "task_path": str(task_path), "packet_path": str(packet_path),
                "template_path": str(template_path), "response_path": str(response_path),
            }],
        }
    response = validate_retrospective_response(packet_path, response_path)
    final_path = run_dir / "retrospective.json"
    _write_json(final_path, {
        "schema_version": RETROSPECTIVE_SCHEMA_VERSION,
        "packet_id": packet_id, "product": analysis.get("product"),
        "trade_date": trade_date, "evaluation": evaluation_artifact,
        "review": response,
    })
    return {
        "result": "RETROSPECTIVE_COMPLETE", **base,
        "retrospective": str(final_path), "actions": [],
    }


def refresh_retrospectives(
    data_root: Path, as_of: date, *, max_source_dates: int = 30,
) -> dict[str, Any]:
    """Refresh eligible historical outcomes without blocking the daily report."""
    if max_source_dates < 1:
        raise ValueError("max_source_dates must be positive")
    candidates: list[tuple[date, str]] = []
    for path in (data_root / "analysis").glob("trade_date=*/analysis.json"):
        value = path.parent.name.removeprefix("trade_date=")
        try:
            source_date = date.fromisoformat(value)
        except ValueError:
            continue
        if source_date < as_of:
            candidates.append((source_date, value))
    results = []
    actions = []
    errors = []
    for _, value in sorted(candidates)[-max_source_dates:]:
        try:
            result = prepare_retrospective(data_root, value)
        except (AnalysisValidationError, ValueError, KeyError) as exc:
            errors.append({"trade_date": value, "error": str(exc)})
            continue
        results.append({"trade_date": value, "result": result["result"]})
        actions.extend(result.get("actions", []))
    return {"as_of": as_of.isoformat(), "results": results, "actions": actions, "errors": errors}


__all__ = [
    "prepare_retrospective", "refresh_retrospectives",
    "validate_retrospective_response",
]

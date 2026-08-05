"""Deterministic outcome evaluation for stable investigator findings."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ccvm.analytics.outcomes import persist_outcome, realize_outcome
from ccvm.schemas.learning import InvestigatorEvaluationRecord

INVESTIGATOR_EVALUATOR_VERSION = 1
_MATERIALITY_LABELS = ("muted", "material", "extreme")
_EXPECTED_SCORES = {"low": 0, "medium": 1, "high": 2}


def _outcome_id(finding_id: str, dimension: str) -> str:
    digest = hashlib.sha256(f"{finding_id}|{dimension}".encode()).hexdigest()[:24]
    return f"investigator-outcome:{digest}"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_investigator_findings(
    investigator_analyses: Mapping[str, Mapping[str, Any]],
    feedback: Iterable[Mapping[str, Any]],
    *, contract: Mapping[str, Any], source_date: date, reports_dir: Path,
    outcomes_dir: Path, analysis_path: Path, analysis_sha256: str,
    evaluated_at: datetime | None = None,
) -> list[InvestigatorEvaluationRecord]:
    """Score materiality and lead use without interpreting finding prose."""
    dimensions = contract.get("dimensions")
    horizons = set(contract.get("horizons_sessions") or [])
    if not isinstance(dimensions, Mapping) or not horizons:
        raise ValueError("investigator relevance contract is incomplete")
    feedback_by_id = {
        str(item.get("investigation_id")): item for item in feedback
        if isinstance(item, Mapping)
    }
    now = evaluated_at or datetime.now(timezone.utc)
    records: list[InvestigatorEvaluationRecord] = []
    for role, response in investigator_analyses.items():
        investigation_id = str(response.get("investigation_id", ""))
        review = feedback_by_id.get(investigation_id, {})
        disposition = str(review.get("disposition", "rejected"))
        if disposition not in {"used", "partially_used", "rejected"}:
            disposition = "rejected"
        used_ids = set(review.get("used_finding_ids") or [])
        for finding in response.get("candidate_findings") or []:
            finding_id = str(finding.get("finding_id", ""))
            finding_horizon = int(finding.get("horizon_sessions", 0))
            impact_dimensions = list(finding.get("expected_impact_dimensions") or [])
            dimension_results: list[dict[str, Any]] = []
            outcome_hashes: list[str] = []
            scores: list[int] = []
            unavailable = ""
            if finding_horizon not in horizons:
                unavailable = f"finding horizon is not configured: {finding_horizon}"
            for dimension in impact_dimensions if not unavailable else []:
                definition = dimensions.get(dimension)
                if not isinstance(definition, Mapping):
                    unavailable = f"finding impact dimension is not configured: {dimension}"
                    break
                thresholds = (definition.get("thresholds_by_horizon") or {}).get(
                    str(finding_horizon)
                )
                if not isinstance(thresholds, list) or len(thresholds) != 2 \
                        or float(thresholds[0]) >= float(thresholds[1]):
                    raise ValueError(
                        f"investigator materiality thresholds are invalid: {dimension}"
                    )
                outcome_id = _outcome_id(finding_id, dimension)
                synthetic_forecast = {
                    "forecast_id": outcome_id,
                    "source_view_rank": 1,
                    "dimension": dimension,
                    "metric_key": definition["metric_key"],
                    "horizon_sessions": finding_horizon,
                    "expected_label": definition["labels"][0],
                    "confidence": finding.get("confidence", "low"),
                    "evidence_ids": finding.get("evidence_ids") or [],
                }
                outcome = realize_outcome(
                    synthetic_forecast,
                    {"version": contract.get("version", 1), "dimensions": dimensions},
                    source_date, reports_dir, analysis_path=analysis_path,
                    generated_at=now,
                )
                outcome_path = outcomes_dir / f"{outcome_id.split(':', 1)[1]}.json"
                outcome = persist_outcome(outcome, outcome_path)
                outcome_hashes.append(_hash(outcome_path))
                if outcome.status != "complete" or not outcome.metrics \
                        or outcome.metrics[0].change is None:
                    unavailable = f"outcome status is {outcome.status} for {dimension}"
                    break
                change = abs(float(outcome.metrics[0].change))
                score = 0 if change < float(thresholds[0]) else (
                    1 if change < float(thresholds[1]) else 2
                )
                scores.append(score)
                dimension_results.append({
                    "dimension": dimension,
                    "absolute_change": change,
                    "realized_materiality": _MATERIALITY_LABELS[score],
                    "outcome_id": outcome_id,
                })
            common = {
                "finding_id": finding_id,
                "investigation_id": investigation_id,
                "role": role,
                "horizon_sessions": finding_horizon,
                "expected_materiality": finding.get("materiality"),
                "expected_impact_dimensions": impact_dimensions,
                "confidence": finding.get("confidence"),
                "lead_disposition": disposition,
                "lead_used_finding": finding_id in used_ids,
                "dimension_results": dimension_results,
                "evidence_validation_passed": True,
                "analysis_sha256": analysis_sha256,
                "outcome_sha256": sorted(set(outcome_hashes)),
                "evaluated_at": now,
                "evaluator_version": INVESTIGATOR_EVALUATOR_VERSION,
            }
            if unavailable or not scores:
                records.append(InvestigatorEvaluationRecord(
                    **common, status="unscored",
                    unscored_reason=unavailable or "finding has no impact dimensions",
                ))
                continue
            score = max(scores)
            material = score >= 1
            records.append(InvestigatorEvaluationRecord(
                **common, status="scored", realized_materiality=_MATERIALITY_LABELS[score],
                materiality_score=score,
                materiality_hit=score == _EXPECTED_SCORES[finding["materiality"]],
                material=material,
                rejected_but_material=finding_id not in used_ids and material,
            ))
    return records


def aggregate_investigator_evaluations(
    records: Iterable[InvestigatorEvaluationRecord | Mapping[str, Any]],
) -> dict[str, Any]:
    models = [
        item if isinstance(item, InvestigatorEvaluationRecord)
        else InvestigatorEvaluationRecord.model_validate(item)
        for item in records
    ]
    scored = [item for item in models if item.status == "scored"]
    used = [item for item in scored if item.lead_used_finding]
    rejected = [item for item in scored if not item.lead_used_finding]
    return {
        "evaluator_version": INVESTIGATOR_EVALUATOR_VERSION,
        "n": len(models),
        "scored": len(scored),
        "material_rate": round(
            sum(bool(item.material) for item in scored) / len(scored), 6
        ) if scored else None,
        "materiality_hit_rate": round(
            sum(bool(item.materiality_hit) for item in scored) / len(scored), 6
        ) if scored else None,
        "lead_use_rate": round(len(used) / len(scored), 6) if scored else None,
        "used_material_rate": round(
            sum(bool(item.material) for item in used) / len(used), 6
        ) if used else None,
        "rejected_material_rate": round(
            sum(bool(item.material) for item in rejected) / len(rejected), 6
        ) if rejected else None,
    }


__all__ = [
    "INVESTIGATOR_EVALUATOR_VERSION", "aggregate_investigator_evaluations",
    "evaluate_investigator_findings",
]

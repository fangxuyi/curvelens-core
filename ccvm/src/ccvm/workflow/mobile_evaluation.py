"""Deterministic evaluation of ex-ante mobile editorial decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ccvm.schemas.learning import ForecastLedgerItem, OutcomeRecord
from ccvm.schemas.reporting import MobileRelevanceEvaluation, MobileSelection

MOBILE_RELEVANCE_EVALUATOR_VERSION = 1
_MATERIALITY_LABELS = ("muted", "material", "extreme")


def _unscored(
    *, candidate: Any, reason: str, analysis_sha256: str,
    outcome_hashes: list[str], evaluated_at: datetime,
    dimension_results: list[dict[str, Any]],
) -> MobileRelevanceEvaluation:
    return MobileRelevanceEvaluation(
        source_view_rank=candidate.source_view_rank,
        disposition=candidate.disposition,
        expected_materiality=candidate.materiality,
        expected_impact_dimensions=candidate.expected_impact_dimensions,
        status="unscored", unscored_reason=reason,
        dimension_results=dimension_results,
        analysis_sha256=analysis_sha256,
        outcome_sha256=sorted(set(outcome_hashes)),
        evaluated_at=evaluated_at,
        evaluator_version=MOBILE_RELEVANCE_EVALUATOR_VERSION,
    )


def evaluate_mobile_selection(
    selection: MobileSelection | Mapping[str, Any],
    forecasts: Iterable[ForecastLedgerItem | Mapping[str, Any]],
    outcomes: Iterable[OutcomeRecord | Mapping[str, Any]],
    *, contract: Mapping[str, Any], analysis_sha256: str,
    outcome_hashes: Mapping[str, str], evaluated_at: datetime | None = None,
) -> list[MobileRelevanceEvaluation]:
    """Score selected and omitted views using only configured one-session moves."""
    selection = (
        selection if isinstance(selection, MobileSelection)
        else MobileSelection.model_validate(selection)
    )
    horizon = int(contract.get("horizon_sessions", 0))
    dimensions = contract.get("dimensions")
    if horizon != 1 or not isinstance(dimensions, Mapping):
        raise ValueError("mobile relevance contract is incomplete")
    forecast_models = [
        item if isinstance(item, ForecastLedgerItem)
        else ForecastLedgerItem.model_validate(item)
        for item in forecasts
    ]
    outcome_models = [
        item if isinstance(item, OutcomeRecord) else OutcomeRecord.model_validate(item)
        for item in outcomes
    ]
    forecast_map = {
        (item.source_view_rank, item.dimension, item.horizon_sessions): item
        for item in forecast_models
    }
    outcome_map = {item.forecast_id: item for item in outcome_models}
    now = evaluated_at or datetime.now(timezone.utc)
    records = []
    for candidate in selection.candidates:
        results: list[dict[str, Any]] = []
        hashes: list[str] = []
        scores: list[int] = []
        unavailable = ""
        for dimension in candidate.expected_impact_dimensions:
            definition = dimensions.get(dimension)
            if not isinstance(definition, Mapping):
                unavailable = f"mobile materiality dimension is not configured: {dimension}"
                break
            thresholds = definition.get("thresholds")
            if not isinstance(thresholds, list) or len(thresholds) != 2 \
                    or float(thresholds[0]) >= float(thresholds[1]):
                raise ValueError(f"mobile materiality thresholds are invalid: {dimension}")
            forecast = forecast_map.get((candidate.source_view_rank, dimension, horizon))
            if forecast is None:
                unavailable = f"no {horizon}-session forecast for {dimension}"
                break
            outcome = outcome_map.get(forecast.forecast_id)
            if outcome is None or outcome.status != "complete" or not outcome.metrics \
                    or outcome.metrics[0].change is None:
                state = outcome.status if outcome is not None else "missing"
                unavailable = f"outcome status is {state} for {dimension}"
                break
            change = abs(float(outcome.metrics[0].change))
            score = 0 if change < float(thresholds[0]) else (
                1 if change < float(thresholds[1]) else 2
            )
            digest = outcome_hashes.get(forecast.forecast_id, "")
            if digest:
                hashes.append(digest)
            scores.append(score)
            results.append({
                "dimension": dimension, "absolute_change": change,
                "realized_materiality": _MATERIALITY_LABELS[score],
                "forecast_id": forecast.forecast_id,
            })
        if unavailable:
            records.append(_unscored(
                candidate=candidate, reason=unavailable,
                analysis_sha256=analysis_sha256, outcome_hashes=hashes,
                evaluated_at=now, dimension_results=results,
            ))
            continue
        score = max(scores)
        material = score >= 1
        selected = candidate.disposition == "selected"
        records.append(MobileRelevanceEvaluation(
            source_view_rank=candidate.source_view_rank,
            disposition=candidate.disposition,
            expected_materiality=candidate.materiality,
            expected_impact_dimensions=candidate.expected_impact_dimensions,
            status="scored", realized_materiality=_MATERIALITY_LABELS[score],
            materiality_score=score, selection_correct=selected == material,
            missed_material=not selected and material,
            false_prominence=selected and not material,
            dimension_results=results, analysis_sha256=analysis_sha256,
            outcome_sha256=sorted(set(hashes)), evaluated_at=now,
            evaluator_version=MOBILE_RELEVANCE_EVALUATOR_VERSION,
        ))
    return records


def aggregate_mobile_relevance(
    records: Iterable[MobileRelevanceEvaluation | Mapping[str, Any]],
) -> dict[str, Any]:
    models = [
        item if isinstance(item, MobileRelevanceEvaluation)
        else MobileRelevanceEvaluation.model_validate(item)
        for item in records
    ]
    scored = [item for item in models if item.status == "scored"]
    selected = [item for item in scored if item.disposition == "selected"]
    omitted = [item for item in scored if item.disposition == "omitted"]
    material_selected = sum(item.materiality_score >= 1 for item in selected)
    missed = sum(bool(item.missed_material) for item in omitted)
    false_prominence = sum(bool(item.false_prominence) for item in selected)
    return {
        "evaluator_version": MOBILE_RELEVANCE_EVALUATOR_VERSION,
        "n": len(models), "scored": len(scored),
        "selected_scored": len(selected), "omitted_scored": len(omitted),
        "precision": round(material_selected / len(selected), 6) if selected else None,
        "missed_material_rate": round(missed / len(omitted), 6) if omitted else None,
        "false_prominence_rate": round(
            false_prominence / len(selected), 6
        ) if selected else None,
        "selection_accuracy": round(
            sum(bool(item.selection_correct) for item in scored) / len(scored), 6
        ) if scored else None,
    }


__all__ = [
    "MOBILE_RELEVANCE_EVALUATOR_VERSION", "aggregate_mobile_relevance",
    "evaluate_mobile_selection",
]

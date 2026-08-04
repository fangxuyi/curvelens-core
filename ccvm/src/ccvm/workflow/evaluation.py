"""Deterministic forecast evaluation without post-hoc model judgment."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ccvm.schemas.learning import EvaluationRecord, ForecastLedgerItem, OutcomeRecord

EVALUATOR_VERSION = 1
_CONFIDENCE = {"high": 0.80, "medium": 0.65, "low": 0.55}
_RANK_WEIGHT = {1: 1.0, 2: 0.7, 3: 0.4}


def evaluate_forecast(
    forecast: ForecastLedgerItem | Mapping[str, Any],
    outcome: OutcomeRecord | Mapping[str, Any],
    *, analysis_sha256: str, outcome_sha256: str,
    evaluated_at: datetime | None = None,
) -> EvaluationRecord:
    forecast = (
        forecast if isinstance(forecast, ForecastLedgerItem)
        else ForecastLedgerItem.model_validate(forecast)
    )
    outcome = (
        outcome if isinstance(outcome, OutcomeRecord)
        else OutcomeRecord.model_validate(outcome)
    )
    if outcome.forecast_id != forecast.forecast_id:
        raise ValueError("forecast and outcome IDs do not match")
    if outcome.dimension != forecast.dimension \
            or outcome.horizon_sessions != forecast.horizon_sessions:
        raise ValueError("forecast and outcome contracts do not match")
    now = evaluated_at or datetime.now(timezone.utc)
    base = {
        "forecast_id": forecast.forecast_id,
        "dimension": forecast.dimension,
        "horizon_sessions": forecast.horizon_sessions,
        "source_view_rank": forecast.source_view_rank,
        "confidence": forecast.confidence,
        "expected_label": forecast.expected_label,
        "rank_weight": _RANK_WEIGHT[forecast.source_view_rank],
        "analysis_sha256": analysis_sha256,
        "outcome_sha256": outcome_sha256,
        "evaluated_at": now,
        "evaluator_version": EVALUATOR_VERSION,
    }
    metric = outcome.metrics[0] if outcome.metrics else None
    if outcome.status != "complete" or metric is None or metric.realized_label is None:
        return EvaluationRecord(
            **base, status="unscored",
            unscored_reason=f"outcome status is {outcome.status}",
        )
    hit = forecast.expected_label == metric.realized_label
    probability = _CONFIDENCE[forecast.confidence]
    brier = (probability - float(hit)) ** 2
    return EvaluationRecord(
        **base, status="scored", realized_label=metric.realized_label,
        hit=hit, confidence_probability=probability,
        brier_loss=round(brier, 6),
        weighted_hit=_RANK_WEIGHT[forecast.source_view_rank] * float(hit),
    )


def aggregate_evaluations(
    records: Iterable[EvaluationRecord | Mapping[str, Any]], *, min_samples: int = 5,
) -> dict[str, Any]:
    if min_samples < 1:
        raise ValueError("min_samples must be positive")
    groups: dict[tuple[str, int, str], list[EvaluationRecord]] = defaultdict(list)
    for raw in records:
        record = raw if isinstance(raw, EvaluationRecord) else EvaluationRecord.model_validate(raw)
        groups[(record.dimension, record.horizon_sessions, record.confidence)].append(record)
    rows = []
    for (dimension, horizon, confidence), values in sorted(groups.items()):
        scored = [item for item in values if item.status == "scored"]
        hits = sum(bool(item.hit) for item in scored)
        weight_total = sum(item.rank_weight for item in scored)
        rows.append({
            "dimension": dimension,
            "horizon_sessions": horizon,
            "confidence": confidence,
            "n": len(values),
            "scored": len(scored),
            "hits": hits,
            "sample_ready": len(scored) >= min_samples,
            "hit_rate": round(hits / len(scored), 6) if scored else None,
            "mean_brier": round(
                sum(item.brier_loss or 0 for item in scored) / len(scored), 6
            ) if scored else None,
            "weighted_hit_rate": round(
                sum(item.weighted_hit or 0 for item in scored) / weight_total, 6
            ) if weight_total else None,
        })
    return {"evaluator_version": EVALUATOR_VERSION, "min_samples": min_samples, "groups": rows}


__all__ = ["EVALUATOR_VERSION", "aggregate_evaluations", "evaluate_forecast"]

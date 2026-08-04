from datetime import date, datetime, timezone

from ccvm.schemas.learning import OutcomeMetric, OutcomeRecord
from ccvm.workflow.mobile_evaluation import (
    aggregate_mobile_relevance,
    evaluate_mobile_selection,
)

HASH = "a" * 64
CONTRACT = {
    "version": 1,
    "horizon_sessions": 1,
    "dimensions": {"price_direction": {"thresholds": [0.005, 0.015]}},
}


def _selection(selected=(1,)):
    return {
        "selected_view_ranks": list(selected),
        "selection_rationale": "The selected view has the highest expected impact.",
        "candidates": [{
            "source_view_rank": rank,
            "disposition": "selected" if rank in selected else "omitted",
            "materiality": "high" if rank in selected else "medium",
            "expected_impact_dimensions": ["price_direction"],
            "rationale": "The current evidence supports this ex-ante classification.",
            "evidence_ids": [f"feature:view-{rank}"],
        } for rank in (1, 2, 3)],
        "limitation_disposition": "not_applicable",
        "limitation_rationale": "No material limitation was reported.",
    }


def _forecast(rank):
    return {
        "forecast_id": f"packet:v{rank}:price_direction:h1",
        "source_view_rank": rank, "dimension": "price_direction",
        "metric_key": "front_settlement_return", "horizon_sessions": 1,
        "expected_label": "up", "confidence": "medium",
        "evidence_ids": [f"feature:view-{rank}"],
    }


def _outcome(rank, change, status="complete"):
    metric = OutcomeMetric(
        metric_key="front_settlement_return",
        baseline=100 if status == "complete" else None,
        target=(100 * (1 + change)) if status == "complete" else None,
        change=change if status == "complete" else None,
        realized_label="up" if status == "complete" else None,
    )
    return OutcomeRecord(
        forecast_id=f"packet:v{rank}:price_direction:h1",
        dimension="price_direction", expected_label="up",
        source_trade_date=date(2026, 7, 2), target_date=date(2026, 7, 6),
        horizon_sessions=1, status=status, metrics=[metric],
        analysis_sha256=HASH, baseline_sha256=HASH,
        target_sha256=HASH if status == "complete" else None,
        policy_sha256=HASH, policy_version=2,
        generated_at=datetime.now(timezone.utc), record_version=1,
    )


def test_selected_and_omitted_views_receive_distinct_relevance_scores():
    forecasts = [_forecast(rank) for rank in (1, 2, 3)]
    outcomes = [_outcome(1, 0.01), _outcome(2, 0.02), _outcome(3, 0.001)]
    records = evaluate_mobile_selection(
        _selection(), forecasts, outcomes, contract=CONTRACT,
        analysis_sha256=HASH,
        outcome_hashes={item["forecast_id"]: HASH for item in forecasts},
    )

    assert records[0].realized_materiality == "material"
    assert records[0].selection_correct is True
    assert records[1].realized_materiality == "extreme"
    assert records[1].missed_material is True
    assert records[2].selection_correct is True
    aggregate = aggregate_mobile_relevance(records)
    assert aggregate["precision"] == 1
    assert aggregate["missed_material_rate"] == 0.5
    assert aggregate["selection_accuracy"] == 0.666667


def test_selected_muted_view_is_false_prominence():
    records = evaluate_mobile_selection(
        _selection(), [_forecast(rank) for rank in (1, 2, 3)],
        [_outcome(rank, 0.001) for rank in (1, 2, 3)], contract=CONTRACT,
        analysis_sha256=HASH,
        outcome_hashes={f"packet:v{rank}:price_direction:h1": HASH for rank in (1, 2, 3)},
    )

    assert records[0].false_prominence is True
    assert aggregate_mobile_relevance(records)["false_prominence_rate"] == 1


def test_pending_outcome_leaves_candidate_unscored():
    records = evaluate_mobile_selection(
        _selection(), [_forecast(1)], [_outcome(1, 0, status="pending")],
        contract=CONTRACT, analysis_sha256=HASH,
        outcome_hashes={"packet:v1:price_direction:h1": HASH},
    )

    assert records[0].status == "unscored"
    assert "pending" in records[0].unscored_reason

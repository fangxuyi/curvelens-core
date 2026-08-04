from datetime import date, datetime, timezone

import pytest

from ccvm.schemas.learning import OutcomeMetric, OutcomeRecord
from ccvm.workflow.evaluation import aggregate_evaluations, evaluate_forecast

HASH = "a" * 64


def _forecast(**overrides):
    value = {
        "forecast_id": "packet:v1:price_direction:h1",
        "source_view_rank": 1,
        "dimension": "price_direction",
        "metric_key": "front_settlement_return",
        "horizon_sessions": 1,
        "expected_label": "up",
        "confidence": "high",
        "evidence_ids": ["feature:market:2026-07-02"],
    }
    value.update(overrides)
    return value


def _outcome(status="complete", label="up"):
    metric = OutcomeMetric(
        metric_key="front_settlement_return",
        baseline=100, target=101, change=0.01,
        realized_label=label,
    )
    if status != "complete":
        metric = OutcomeMetric(metric_key="front_settlement_return")
    return OutcomeRecord(
        forecast_id="packet:v1:price_direction:h1",
        dimension="price_direction", expected_label="up",
        source_trade_date=date(2026, 7, 2), target_date=date(2026, 7, 6),
        horizon_sessions=1, status=status, metrics=[metric],
        analysis_sha256=HASH, baseline_sha256=HASH, target_sha256=HASH,
        policy_sha256=HASH, policy_version=2,
        generated_at=datetime.now(timezone.utc), record_version=1,
    )


def test_scored_hit_uses_confidence_and_rank_weight():
    record = evaluate_forecast(
        _forecast(), _outcome(), analysis_sha256=HASH, outcome_sha256=HASH,
    )
    assert record.status == "scored" and record.hit is True
    assert record.confidence_probability == 0.8
    assert record.brier_loss == pytest.approx(0.04)
    assert record.weighted_hit == 1.0
    assert record.association == "forecast_associated_with_realized_outcome"


def test_scored_miss_and_lower_rank_weight():
    record = evaluate_forecast(
        _forecast(source_view_rank=3, confidence="low"),
        _outcome(label="down"), analysis_sha256=HASH, outcome_sha256=HASH,
    )
    assert record.hit is False and record.brier_loss == pytest.approx(0.3025)
    assert record.rank_weight == 0.4 and record.weighted_hit == 0


@pytest.mark.parametrize("status", ["pending", "missing"])
def test_unavailable_outcomes_are_unscored(status):
    record = evaluate_forecast(
        _forecast(), _outcome(status=status, label=None),
        analysis_sha256=HASH, outcome_sha256=HASH,
    )
    assert record.status == "unscored"
    assert record.hit is None and record.brier_loss is None
    assert status in record.unscored_reason


def test_mismatched_outcome_is_rejected():
    outcome = _outcome().model_copy(update={"forecast_id": "different:v1:item:h1"})
    with pytest.raises(ValueError, match="IDs do not match"):
        evaluate_forecast(
            _forecast(), outcome, analysis_sha256=HASH, outcome_sha256=HASH,
        )


def test_aggregation_is_generic_and_sample_gated():
    records = [
        evaluate_forecast(
            _forecast(forecast_id=f"packet:v1:price_direction:h1-{index}"),
            _outcome().model_copy(update={
                "forecast_id": f"packet:v1:price_direction:h1-{index}",
            }),
            analysis_sha256=HASH, outcome_sha256=HASH,
        )
        for index in range(3)
    ]
    result = aggregate_evaluations(records, min_samples=3)
    row = result["groups"][0]
    assert row["sample_ready"] is True
    assert row["hit_rate"] == 1 and row["mean_brier"] == pytest.approx(0.04)


def test_aggregation_retains_unscored_counts():
    scored = evaluate_forecast(
        _forecast(), _outcome(), analysis_sha256=HASH, outcome_sha256=HASH,
    )
    pending = evaluate_forecast(
        _forecast(forecast_id="packet:v1:price_direction:h1-pending"),
        _outcome(status="pending", label=None).model_copy(update={
            "forecast_id": "packet:v1:price_direction:h1-pending",
        }),
        analysis_sha256=HASH, outcome_sha256=HASH,
    )
    row = aggregate_evaluations([scored, pending])["groups"][0]
    assert row["n"] == 2 and row["scored"] == 1 and row["hits"] == 1

import pytest
from pydantic import ValidationError

from ccvm.schemas.learning import (
    ForecastLedgerItem,
    MemoryFeedbackItem,
    MobileLearningAdvisory,
    InvestigatorLearningAdvisory,
)


def forecast_payload(**overrides):
    payload = {
        "forecast_id": "packet-001:v1:curve_shape:h5",
        "source_view_rank": 1,
        "dimension": "curve_shape",
        "metric_key": "front_spread",
        "horizon_sessions": 5,
        "expected_label": "widening",
        "confidence": "medium",
        "evidence_ids": ["feature:curve:2026-08-03", "news:abc123"],
    }
    payload.update(overrides)
    return payload


def memory_payload(**overrides):
    payload = {
        "advisory_id": "advisory-001",
        "disposition": "used",
        "rationale": "The advisory matched the observed evidence and was retained.",
        "evidence_ids": [],
    }
    payload.update(overrides)
    return payload


def mobile_advisory_payload(**overrides):
    payload = {
        "advisory_id": "mobile-learning:abc123",
        "status": "candidate",
        "scope": {
            "source_view_rank": 1, "expected_materiality": "high",
            "impact_dimensions": ["price_direction", "volatility_direction"],
        },
        "recommendation": "prefer_select",
        "observation": "This scope was materially relevant in 70% of samples.",
        "suggested_adjustment": "Prefer selection when current evidence matches.",
        "sample_size": 20, "material_count": 14, "material_rate": 0.7,
        "selection_accuracy": 0.65, "missed_material_rate": 0.1,
        "false_prominence_rate": 0.15, "promotion_eligible": True,
        "source_evaluation_sha256": ["a" * 64],
        "created_at": "2026-08-01T12:00:00+00:00",
        "updated_at": "2026-08-03T12:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_forecast_ledger_item_validates_product_neutral_keys_and_ids():
    item = ForecastLedgerItem.model_validate(forecast_payload())

    assert item.source_view_rank == 1
    assert item.expected_label == "widening"
    assert item.evidence_ids == ["feature:curve:2026-08-03", "news:abc123"]


def test_memory_feedback_item_allows_empty_evidence():
    item = MemoryFeedbackItem.model_validate(memory_payload())

    assert item.disposition == "used"
    assert item.evidence_ids == []


@pytest.mark.parametrize("field,value", [
    ("source_view_rank", 0),
    ("source_view_rank", 4),
    ("horizon_sessions", 0),
    ("horizon_sessions", 253),
])
def test_forecast_bounds_are_enforced(field, value):
    with pytest.raises(ValidationError):
        ForecastLedgerItem(**forecast_payload(**{field: value}))


@pytest.mark.parametrize("field,value", [
    ("dimension", "curve shape"),
    ("metric_key", "../metric"),
    ("expected_label", "N/A"),
    ("forecast_id", "TODO"),
])
def test_unsafe_or_placeholder_forecast_values_are_rejected(field, value):
    with pytest.raises(ValidationError):
        ForecastLedgerItem(**forecast_payload(**{field: value}))


def test_forecast_requires_unique_nonempty_evidence_ids():
    with pytest.raises(ValidationError):
        ForecastLedgerItem(**forecast_payload(evidence_ids=["feature:curve", "feature:curve"]))

    with pytest.raises(ValidationError):
        ForecastLedgerItem(**forecast_payload(evidence_ids=[]))


def test_unsafe_evidence_id_is_rejected():
    with pytest.raises(ValidationError):
        ForecastLedgerItem(**forecast_payload(evidence_ids=["feature:../secret"]))


@pytest.mark.parametrize("field,value", [
    ("advisory_id", "unknown"),
    ("disposition", "pending"),
    ("rationale", "   "),
    ("rationale", "N/A"),
])
def test_memory_feedback_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        MemoryFeedbackItem(**memory_payload(**{field: value}))


def test_learning_items_forbid_unexpected_fields():
    with pytest.raises(ValidationError):
        ForecastLedgerItem(**forecast_payload(unexpected="value"))


def test_mobile_learning_advisory_has_strict_stable_scope():
    advisory = MobileLearningAdvisory.model_validate(mobile_advisory_payload())

    assert advisory.recommendation == "prefer_select"
    assert advisory.scope.impact_dimensions == [
        "price_direction", "volatility_direction",
    ]


def test_mobile_learning_advisory_rejects_unstable_scope_and_counts():
    value = mobile_advisory_payload()
    value["scope"]["impact_dimensions"] = ["volatility_direction", "price_direction"]
    with pytest.raises(ValidationError, match="unique and sorted"):
        MobileLearningAdvisory.model_validate(value)

    with pytest.raises(ValidationError, match="cannot exceed"):
        MobileLearningAdvisory.model_validate(
            mobile_advisory_payload(material_count=21)
        )

    with pytest.raises(ValidationError, match="neutral mobile advice"):
        MobileLearningAdvisory.model_validate(
            mobile_advisory_payload(recommendation="neutral")
        )


def test_investigator_learning_advisory_has_strict_capability_scope():
    payload = {
        "advisory_id": "investigator-learning:abc123", "status": "candidate",
        "scope": {
            "role": "futures_curve", "horizon_sessions": 1,
            "expected_materiality": "medium",
            "impact_dimensions": ["price_direction"],
        },
        "recommendation": "prefer_dispatch",
        "observation": "Materiality was high in the scored sample.",
        "suggested_adjustment": "Prefer dispatch only when current evidence matches.",
        "sample_size": 20, "material_count": 14, "material_rate": 0.7,
        "materiality_hit_rate": 0.6, "lead_use_rate": 0.5,
        "rejected_material_rate": 0.1, "promotion_eligible": True,
        "source_evaluation_sha256": ["a" * 64],
        "created_at": "2026-07-19T12:00:00+00:00",
        "updated_at": "2026-07-19T12:00:00+00:00",
    }
    advisory = InvestigatorLearningAdvisory.model_validate(payload)
    assert advisory.scope.role == "futures_curve"
    payload["scope"]["impact_dimensions"] = [
        "volatility_direction", "price_direction",
    ]
    with pytest.raises(ValidationError, match="unique and sorted"):
        InvestigatorLearningAdvisory.model_validate(payload)

    payload["scope"]["impact_dimensions"] = ["price_direction"]
    payload["material_rate"] = 0.6
    with pytest.raises(ValidationError, match="must match"):
        InvestigatorLearningAdvisory.model_validate(payload)

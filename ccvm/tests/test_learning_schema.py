import pytest
from pydantic import ValidationError

from ccvm.schemas.learning import ForecastLedgerItem, MemoryFeedbackItem


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

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from ccvm.analytics.outcomes import (
    apply_outcome_rule,
    nth_future_business_session,
    persist_outcome,
    realize_outcome,
)
from ccvm.schemas.learning import OutcomeRecord, OutcomeRule


CONTRACT = {
    "version": 2,
    "dimensions": {
        "price_direction": {
            "metric_key": "front_settlement_return",
            "labels": ["down", "flat", "up"],
            "outcome_rule": {
                "source_metric": "front_settlement",
                "calculation": "return",
                "kind": "signed_band",
                "thresholds": [0.0025],
                "labels": ["down", "flat", "up"],
            },
        },
        "market_impact": {
            "metric_key": "absolute_front_settlement_return",
            "labels": ["muted", "material", "extreme"],
            "outcome_rule": {
                "source_metric": "front_settlement",
                "calculation": "absolute_return",
                "kind": "absolute_bands",
                "thresholds": [0.005, 0.015],
                "labels": ["muted", "material", "extreme"],
            },
        },
    },
}


def _forecast(dimension="price_direction", horizon=1):
    return {
        "forecast_id": f"packet:v1:{dimension}:h{horizon}",
        "dimension": dimension,
        "horizon_sessions": horizon,
        "expected_label": CONTRACT["dimensions"][dimension]["labels"][-1],
    }


def _write_report(path, trade_date, settlement=100.0, atm_iv=0.20):
    path.write_text(json.dumps({
        "trade_date": trade_date,
        "sections": {"market_risk": {
            "futures": {"front_settlement": settlement},
            "options": {"atm_iv": atm_iv},
        }},
    }))


def _realize(tmp_path, *, horizon=1, dimension="price_direction", now="2026-07-10"):
    return realize_outcome(
        _forecast(dimension, horizon), CONTRACT, "2026-07-02", tmp_path,
        analysis_input={"trade_date": "2026-07-02"},
        generated_at=datetime.fromisoformat(f"{now}T12:00:00+00:00"),
    )


def test_business_session_horizons_skip_holiday_and_weekend():
    assert nth_future_business_session(date(2026, 7, 2), 1) == date(2026, 7, 6)
    assert nth_future_business_session(date(2026, 7, 2), 5) == date(2026, 7, 10)
    with pytest.raises(ValueError, match="positive"):
        nth_future_business_session(date(2026, 7, 2), 0)


def test_generic_signed_and_absolute_rules():
    signed = OutcomeRule.model_validate(
        CONTRACT["dimensions"]["price_direction"]["outcome_rule"]
    )
    impact = OutcomeRule.model_validate(
        CONTRACT["dimensions"]["market_impact"]["outcome_rule"]
    )
    assert apply_outcome_rule(100, 100.1, signed)[1] == "flat"
    assert apply_outcome_rule(100, 99, signed)[1] == "down"
    assert apply_outcome_rule(100, 101.6, impact)[1] == "extreme"


def test_complete_outcome_has_values_labels_and_provenance(tmp_path):
    _write_report(tmp_path / "2026-07-02.json", "2026-07-02", 100)
    _write_report(tmp_path / "2026-07-06.json", "2026-07-06", 101)
    record = _realize(tmp_path)
    assert record.status == "complete"
    assert record.target_date == date(2026, 7, 6)
    assert record.metrics[0].change == pytest.approx(0.01)
    assert record.metrics[0].realized_label == "up"
    assert all((record.analysis_sha256, record.baseline_sha256,
                record.target_sha256, record.policy_sha256))


def test_future_target_is_pending_without_fabricated_values(tmp_path):
    _write_report(tmp_path / "2026-07-02.json", "2026-07-02", 100)
    record = _realize(tmp_path, horizon=5, now="2026-07-06")
    assert record.status == "pending"
    assert record.metrics[0].target is None
    assert record.metrics[0].realized_label is None


@pytest.mark.parametrize("target_payload", [
    {"trade_date": "2026-07-05", "sections": {}},
    {"trade_date": "2026-07-06", "sections": {"market_risk": {
        "futures": {"front_settlement": float("nan")}, "options": {"atm_iv": 0.2},
    }}},
])
def test_malformed_or_mismatched_target_is_missing(tmp_path, target_payload):
    _write_report(tmp_path / "2026-07-02.json", "2026-07-02", 100)
    (tmp_path / "2026-07-06.json").write_text(json.dumps(target_payload))
    record = _realize(tmp_path)
    assert record.status == "missing"
    assert record.metrics[0].realized_label is None
    assert record.data_quality_notes


def test_missing_baseline_is_missing_even_before_target_date(tmp_path):
    record = _realize(tmp_path, horizon=5, now="2026-07-06")
    assert record.status == "missing"
    assert any("baseline" in note for note in record.data_quality_notes)


def test_policy_labels_must_match_rule_labels(tmp_path):
    _write_report(tmp_path / "2026-07-02.json", "2026-07-02", 100)
    _write_report(tmp_path / "2026-07-06.json", "2026-07-06", 101)
    contract = json.loads(json.dumps(CONTRACT))
    contract["dimensions"]["price_direction"]["labels"] = ["bear", "flat", "bull"]
    with pytest.raises(ValueError, match="labels do not match"):
        realize_outcome(
            _forecast(), contract, "2026-07-02", tmp_path,
            analysis_input={"trade_date": "2026-07-02"},
        )


def test_persistence_is_idempotent_and_versions_source_corrections(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_report(reports / "2026-07-02.json", "2026-07-02", 100)
    target = reports / "2026-07-06.json"
    _write_report(target, "2026-07-06", 101)
    current = tmp_path / "learning" / "outcome.json"

    first = persist_outcome(_realize(reports), current)
    duplicate = persist_outcome(_realize(reports, now="2026-07-11"), current)
    assert duplicate.generated_at == first.generated_at
    assert duplicate.record_version == 1

    _write_report(target, "2026-07-06", 102)
    corrected = persist_outcome(_realize(reports, now="2026-07-11"), current)
    assert corrected.record_version == 2
    assert corrected.supersedes_hash
    assert (current.parent / "versions" / "outcome.v1.json").exists()


def test_pending_record_versions_when_window_becomes_missing(tmp_path):
    _write_report(tmp_path / "2026-07-02.json", "2026-07-02", 100)
    current = tmp_path / "outcome.json"
    pending = persist_outcome(_realize(tmp_path, horizon=5, now="2026-07-06"), current)
    missing = persist_outcome(_realize(tmp_path, horizon=5, now="2026-07-11"), current)
    assert pending.status == "pending"
    assert missing.status == "missing" and missing.record_version == 2


def test_outcome_schema_rejects_same_day_and_incomplete_complete_record(tmp_path):
    _write_report(tmp_path / "2026-07-02.json", "2026-07-02", 100)
    _write_report(tmp_path / "2026-07-06.json", "2026-07-06", 101)
    payload = _realize(tmp_path).model_dump()
    payload["target_date"] = payload["source_trade_date"]
    with pytest.raises(ValidationError, match="later"):
        OutcomeRecord.model_validate(payload)
    payload = _realize(tmp_path).model_dump()
    payload["metrics"][0]["realized_label"] = None
    with pytest.raises(ValidationError, match="complete outcomes"):
        OutcomeRecord.model_validate(payload)

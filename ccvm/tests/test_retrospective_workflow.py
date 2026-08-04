from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from ccvm.workflow.finalize import AnalysisValidationError
from ccvm.workflow.retrospective import (
    prepare_retrospective,
    validate_retrospective_response,
)


def _contract():
    return {
        "version": 2,
        "dimensions": {"price_direction": {
            "metric_key": "front_settlement_return",
            "labels": ["down", "flat", "up"],
            "outcome_rule": {
                "source_metric": "front_settlement", "calculation": "return",
                "kind": "signed_band", "thresholds": [0.0025],
                "labels": ["down", "flat", "up"],
            },
        }},
    }


def _setup(root: Path, *, target=True):
    trade_date = "2026-07-02"
    analysis_dir = root / "analysis" / f"trade_date={trade_date}"
    analysis_dir.mkdir(parents=True)
    forecast = {
        "forecast_id": "packet:v1:price_direction:h1",
        "source_view_rank": 1, "dimension": "price_direction",
        "metric_key": "front_settlement_return", "horizon_sessions": 1,
        "expected_label": "up", "confidence": "high",
        "evidence_ids": ["feature:market:2026-07-02"],
    }
    (analysis_dir / "analysis.json").write_text(json.dumps({
        "product": "gold", "trade_date": trade_date,
        "forecast_contract": _contract(),
        "synthesis": {"forecast_ledger": [forecast], "top_views": [{"rank": 1}]},
    }))
    reports = root / "reports"
    reports.mkdir()
    for dt, settle in [(trade_date, 100), ("2026-07-06", 101)]:
        if dt == "2026-07-06" and not target:
            continue
        (reports / f"{dt}.json").write_text(json.dumps({
            "trade_date": dt, "sections": {"market_risk": {
                "futures": {"front_settlement": settle},
                "options": {"atm_iv": 0.2},
            }},
        }))
    workflow = root / "analysis_workflow" / f"trade_date={trade_date}"
    workflow.mkdir(parents=True)
    (workflow / "workflow_events.jsonl").write_text(
        json.dumps({"event": "validation_rejected", "actor": "synthesis"}) + "\n"
        + json.dumps({"event": "run_finalized", "actor": "controller"}) + "\n"
    )
    return trade_date


def test_retrospective_materializes_outcomes_evaluation_and_action(tmp_path):
    trade_date = _setup(tmp_path)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    assert result["result"] == "RETROSPECTIVE_REQUIRED"
    assert result["actions"][0]["agent_type"] == "curvelens_retrospective"
    packet = json.loads(Path(result["packet"]).read_text())
    assert packet["evaluations"][0]["hit"] is True
    assert packet["trace_summary"]["validation_rejections"] == 1
    assert "hidden chain-of-thought" in packet["trace_summary"]["scope_note"]


def test_valid_response_completes_without_activating_candidates(tmp_path):
    trade_date = _setup(tmp_path)
    now = datetime.fromisoformat("2026-07-07T12:00:00+00:00")
    first = prepare_retrospective(tmp_path, trade_date, generated_at=now)
    action = first["actions"][0]
    template = json.loads(Path(action["template_path"]).read_text())
    template.update({
        "status": "complete", "outcome_assessment": "The forecast matched the label.",
        "trace_assessment": "One validation retry was visible.",
        "priority_assessment": "The first-ranked view received full weight.",
    })
    template["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "The stated direction matched.",
        "improvement": "Retain the explicit threshold and evidence link.",
    })
    Path(action["response_path"]).write_text(json.dumps(template))
    result = prepare_retrospective(tmp_path, trade_date, generated_at=now)
    assert result["result"] == "RETROSPECTIVE_COMPLETE"
    final = json.loads(Path(result["retrospective"]).read_text())
    assert final["review"]["candidate_advisories"] == []


def test_response_cannot_override_deterministic_score(tmp_path):
    trade_date = _setup(tmp_path)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    action = result["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
    })
    response["forecast_reviews"][0].update({
        "assessment": "incorrect", "diagnosis": "No.", "improvement": "No.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))
    with pytest.raises(AnalysisValidationError, match="preserve deterministic scoring"):
        validate_retrospective_response(
            Path(action["packet_path"]), Path(action["response_path"]),
        )


def test_retrospective_is_pending_until_target_session_exists(tmp_path):
    trade_date = _setup(tmp_path, target=False)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-03T12:00:00+00:00"),
    )
    assert result["result"] == "RETROSPECTIVE_PENDING"
    assert result["actions"] == []

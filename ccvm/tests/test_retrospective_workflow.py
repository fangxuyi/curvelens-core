from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from ccvm.workflow.finalize import AnalysisValidationError
from ccvm.workflow.retrospective import (
    prepare_retrospective,
    refresh_retrospectives,
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


def _mobile_contract():
    return {
        "version": 1, "horizon_sessions": 1,
        "dimensions": {"price_direction": {"thresholds": [0.005, 0.015]}},
    }


def _investigator_contract():
    return {
        "version": 1, "horizons_sessions": [1, 5],
        "dimensions": {"price_direction": {
            **_contract()["dimensions"]["price_direction"],
            "thresholds_by_horizon": {"1": [0.005, 0.015], "5": [0.01, 0.03]},
        }},
    }


def _mobile_selection():
    return {
        "selected_view_ranks": [1],
        "selection_rationale": "The first view has the highest expected impact.",
        "candidates": [{
            "source_view_rank": rank,
            "disposition": "selected" if rank == 1 else "omitted",
            "materiality": "high" if rank == 1 else "low",
            "expected_impact_dimensions": ["price_direction"],
            "rationale": "Current evidence determined the ex-ante priority.",
            "evidence_ids": ["feature:market:2026-07-02"],
        } for rank in (1, 2, 3)],
        "limitation_disposition": "not_applicable",
        "limitation_rationale": "No material limitation was reported.",
    }


def _setup(root: Path, *, target=True, with_investigator=False):
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
    analysis = {
        "product": "gold", "trade_date": trade_date,
        "forecast_contract": _contract(),
        "mobile_relevance_contract": _mobile_contract(),
        "synthesis": {
            "forecast_ledger": [forecast], "top_views": [{"rank": 1}],
            "mobile_selection": _mobile_selection(),
        },
    }
    if with_investigator:
        finding_id = "packet1234567890:futures_curve:f1"
        investigation_id = "packet1234567890:futures_curve"
        analysis.update({
            "investigator_relevance_contract": _investigator_contract(),
            "investigator_analyses": {"futures_curve": {
                "investigation_id": investigation_id,
                "candidate_findings": [{
                    "finding_id": finding_id, "materiality": "medium",
                    "horizon_sessions": 1, "confidence": "medium",
                    "expected_impact_dimensions": ["price_direction"],
                    "evidence_ids": ["feature:market:2026-07-02"],
                }],
            }},
        })
        analysis["synthesis"]["investigator_feedback"] = [{
            "investigation_id": investigation_id, "disposition": "rejected",
            "used_finding_ids": [],
        }]
    (analysis_dir / "analysis.json").write_text(json.dumps(analysis))
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
    assert packet["mobile_relevance"]["records"][0]["selection_correct"] is True
    assert packet["mobile_relevance"]["aggregate"]["precision"] == 1
    assert "hidden chain-of-thought" in packet["trace_summary"]["scope_note"]
    assert packet["review_contract"]["candidate_rule"].startswith(
        "Leave candidate_advisories empty"
    )
    assert "leave candidate_advisories as an empty list" in Path(
        result["actions"][0]["task_path"]
    ).read_text()


def test_invalid_retrospective_is_archived_and_redispatched(tmp_path):
    trade_date = _setup(tmp_path)
    now = datetime.fromisoformat("2026-07-07T12:00:00+00:00")
    first = prepare_retrospective(tmp_path, trade_date, generated_at=now)
    action = first["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
        "mobile_assessment": "Done.", "investigator_assessment": "Done.",
        "candidate_advisories": [{"candidate_id": "legacy-key"}],
    })
    response["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "appropriate", "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))

    retry = prepare_retrospective(tmp_path, trade_date, generated_at=now)

    assert retry["result"] == "RETROSPECTIVE_REQUIRED"
    assert "candidate_advisories[0]" in retry["validation_error"]
    assert Path(retry["invalid_response"]).exists()
    assert not Path(action["response_path"]).exists()
    assert retry["actions"][0]["response_path"] == action["response_path"]
    assert "prior response failed validation" in Path(action["task_path"]).read_text()


def test_retrospective_scores_rejected_investigator_finding_materiality(tmp_path):
    trade_date = _setup(tmp_path, with_investigator=True)
    result = prepare_retrospective(
        tmp_path, trade_date,
        generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    packet = json.loads(Path(result["packet"]).read_text())
    record = packet["investigator_relevance"]["records"][0]
    assert record["status"] == "scored"
    assert record["realized_materiality"] == "material"
    assert record["materiality_hit"] is True
    assert record["rejected_but_material"] is True
    template = json.loads(Path(result["actions"][0]["template_path"]).read_text())
    assert template["investigator_reviews"][0]["finding_id"] == record["finding_id"]


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
        "mobile_assessment": "The selected view was materially relevant.",
        "investigator_assessment": "No investigator findings were available.",
    })
    template["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "The stated direction matched.",
        "improvement": "Retain the explicit threshold and evidence link.",
    })
    template["mobile_reviews"][0].update({
        "assessment": "appropriate",
        "diagnosis": "The selected view was associated with a material move.",
        "improvement": "Retain the ex-ante materiality threshold.",
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
        "mobile_assessment": "Done.",
        "investigator_assessment": "Done.",
    })
    response["forecast_reviews"][0].update({
        "assessment": "incorrect", "diagnosis": "No.", "improvement": "No.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "appropriate", "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))
    with pytest.raises(AnalysisValidationError, match="preserve deterministic scoring"):
        validate_retrospective_response(
            Path(action["packet_path"]), Path(action["response_path"]),
        )


def test_response_cannot_override_mobile_relevance_score(tmp_path):
    trade_date = _setup(tmp_path)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    action = result["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
        "mobile_assessment": "Done.",
        "investigator_assessment": "Done.",
    })
    response["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "false_prominence", "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))

    with pytest.raises(AnalysisValidationError, match="preserve deterministic relevance scoring"):
        validate_retrospective_response(
            Path(action["packet_path"]), Path(action["response_path"]),
        )


def test_response_cannot_override_investigator_attribution(tmp_path):
    trade_date = _setup(tmp_path, with_investigator=True)
    result = prepare_retrospective(
        tmp_path, trade_date,
        generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    action = result["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
        "mobile_assessment": "Done.", "investigator_assessment": "Done.",
    })
    response["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "appropriate", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["investigator_reviews"][0].update({
        "assessment": "overstated", "lead_disposition": "used",
        "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))
    with pytest.raises(AnalysisValidationError, match="deterministic attribution"):
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


def test_refresh_finds_historical_reviews_without_blocking_on_legacy_files(tmp_path):
    _setup(tmp_path)
    legacy = tmp_path / "analysis" / "trade_date=2026-07-01"
    legacy.mkdir(parents=True)
    (legacy / "analysis.json").write_text(json.dumps({
        "product": "gold", "trade_date": "2026-07-01",
    }))
    result = refresh_retrospectives(tmp_path, date.fromisoformat("2026-07-07"))
    assert result["actions"][0]["action"] == "RUN_RETROSPECTIVE"
    assert result["errors"][0]["trade_date"] == "2026-07-01"

import json
from datetime import date, datetime, timezone

from ccvm.workflow.investigator_evaluation import (
    aggregate_investigator_evaluations,
    evaluate_investigator_findings,
)


def _contract():
    return {
        "version": 1,
        "horizons_sessions": [1, 5],
        "dimensions": {"price_direction": {
            "metric_key": "front_settlement_return",
            "labels": ["down", "flat", "up"],
            "outcome_rule": {
                "source_metric": "front_settlement", "calculation": "return",
                "kind": "signed_band", "thresholds": [0.0025],
                "labels": ["down", "flat", "up"],
            },
            "thresholds_by_horizon": {"1": [0.005, 0.015], "5": [0.01, 0.03]},
        }},
    }


def test_findings_score_materiality_and_rejected_but_material(tmp_path):
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps({"trade_date": "2026-07-02"}))
    reports = tmp_path / "reports"
    reports.mkdir()
    for report_date, settlement in (("2026-07-02", 100), ("2026-07-06", 102)):
        (reports / f"{report_date}.json").write_text(json.dumps({
            "trade_date": report_date,
            "sections": {"market_risk": {
                "futures": {"front_settlement": settlement},
                "options": {"atm_iv": 0.2},
            }},
        }))
    investigation_id = "packet1234567890:futures_curve"
    analyses = {"futures_curve": {
        "investigation_id": investigation_id,
        "candidate_findings": [{
            "finding_id": f"{investigation_id}:f1",
            "materiality": "medium", "horizon_sessions": 1,
            "confidence": "medium",
            "expected_impact_dimensions": ["price_direction"],
            "evidence_ids": ["feature:market:2026-07-02"],
        }],
    }}
    records = evaluate_investigator_findings(
        analyses, [{
            "investigation_id": investigation_id, "disposition": "rejected",
            "used_finding_ids": [],
        }],
        contract=_contract(), source_date=date(2026, 7, 2), reports_dir=reports,
        outcomes_dir=tmp_path / "outcomes", analysis_path=analysis_path,
        analysis_sha256="a" * 64,
        evaluated_at=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )
    record = records[0]
    assert record.realized_materiality == "extreme"
    assert record.materiality_hit is False
    assert record.rejected_but_material is True
    aggregate = aggregate_investigator_evaluations(records)
    assert aggregate["material_rate"] == 1
    assert aggregate["rejected_material_rate"] == 1

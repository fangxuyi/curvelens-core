import json
import importlib.util
from datetime import date
from pathlib import Path

import pytest

from ccvm.workflow.finalize import AnalysisValidationError
from ccvm.workflow.framework_review import (
    finalize_framework_review, framework_review_response_template,
    validate_framework_review_response, write_framework_review_task,
)


def _controller_module():
    path = Path(__file__).resolve().parents[2] / "agent" / "framework_review.py"
    spec = importlib.util.spec_from_file_location("framework_review_controller", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _packet(path: Path) -> dict:
    packet = {
        "schema_version": 1,
        "packet_id": "a" * 64,
        "as_of": "2026-08-05",
        "products_scanned": ["gold", "wti"],
        "workflow_signals": [{
            "signal_id": "framework-signal:abc",
            "framework_eligible": True,
        }],
        "learning_pattern_signals": [{
            "signal_id": "framework-learning-signal:def",
            "framework_eligible": False,
        }],
    }
    path.write_text(json.dumps(packet))
    return packet


def _valid_response(packet: dict) -> dict:
    response = framework_review_response_template(packet)
    response.update({
        "status": "complete",
        "executive_summary": "One shared validation improvement is supported.",
        "boundary_assessment": "Product market learning remains product-local.",
    })
    response["signal_reviews"][0].update({
        "disposition": "propose_framework_change",
        "classification": "shared_validation",
        "evidence_summary": "The same validator contract failed across products.",
        "rationale": "A shared contract clarification can reduce correction pressure.",
        "affected_paths": ["ccvm/src/ccvm/workflow/finalize.py"],
        "proposed_change": "Clarify and validate the shared response contract earlier.",
        "expected_benefit": "Fewer repeated synthesis correction cycles.",
        "risks": "A stricter early check could reject an otherwise usable response.",
        "validation_plan": ["Add a cross-product contract regression test."],
        "rollback_plan": "Revert the contract change if correction rates increase.",
    })
    return response


def test_framework_review_validates_and_renders_advisory_suggestion(tmp_path):
    packet_path = tmp_path / "packet.json"
    packet = _packet(packet_path)
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(_valid_response(packet)))

    response = validate_framework_review_response(packet_path, response_path)
    result = finalize_framework_review(
        packet_path, response_path, tmp_path / "suggestions.json",
        tmp_path / "suggestions.md",
    )

    assert response["signal_reviews"][0]["classification"] == "shared_validation"
    assert result["suggestion_count"] == 1
    suggestion = result["suggestions"][0]
    assert suggestion["suggestion_id"].startswith("framework-suggestion:")
    assert suggestion["requires_human_approval"] is True
    assert suggestion["requires_tested_pull_request"] is True
    assert "Advisory only" in result["authority"]
    assert suggestion["suggestion_id"] in (tmp_path / "suggestions.md").read_text()


def test_framework_review_rejects_product_local_framework_proposal(tmp_path):
    packet_path = tmp_path / "packet.json"
    packet = _packet(packet_path)
    response = _valid_response(packet)
    response["signal_reviews"][0]["classification"] = "product_configuration"
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response))

    with pytest.raises(AnalysisValidationError, match="must target a shared component"):
        validate_framework_review_response(packet_path, response_path)


def test_framework_review_allows_already_resolved_signal_without_change_fields(tmp_path):
    packet_path = tmp_path / "packet.json"
    packet = _packet(packet_path)
    response = _valid_response(packet)
    response["signal_reviews"][0].update({
        "disposition": "already_resolved",
        "classification": "shared_validation",
        "affected_paths": [],
        "proposed_change": "",
        "expected_benefit": "",
        "risks": "",
        "validation_plan": [],
        "rollback_plan": "",
    })
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response))

    validated = validate_framework_review_response(packet_path, response_path)

    assert validated["signal_reviews"][0]["disposition"] == "already_resolved"


@pytest.mark.parametrize("unsafe", ["/tmp/change.py", "../prompt.md", "src/a b.py"])
def test_framework_review_rejects_unsafe_affected_paths(tmp_path, unsafe):
    packet_path = tmp_path / "packet.json"
    packet = _packet(packet_path)
    response = _valid_response(packet)
    response["signal_reviews"][0]["affected_paths"] = [unsafe]
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response))

    with pytest.raises(AnalysisValidationError, match="unsafe repository path"):
        validate_framework_review_response(packet_path, response_path)


def test_framework_review_task_forbids_edits_and_pull_requests(tmp_path):
    task = tmp_path / "task.md"
    write_framework_review_task(
        tmp_path / "packet.json", tmp_path / "template.json",
        tmp_path / "response.json", task,
    )

    text = task.read_text()
    assert "modify no other file" in text
    assert "do not edit code" in text
    assert "pull request" in text


def test_framework_review_controller_completes_a_valid_review(tmp_path, monkeypatch):
    controller = _controller_module()
    ccvm_dir = tmp_path / "ccvm"
    monkeypatch.setattr(controller, "CCVM_DIR", ccvm_dir)
    for product in ("gold", "wti"):
        memory = ccvm_dir / "data" / "products" / product / "learning" / "memory.json"
        memory.parent.mkdir(parents=True)
        memory.write_text(json.dumps({
            "entries": [{
                "status": "candidate",
                "scope": {
                    "dimension": "price_direction", "horizon_sessions": 1,
                    "confidence": "medium",
                },
                "sample_size": 8, "hit_rate": 0.4, "mean_brier": 0.3,
            }],
            "mobile_entries": [], "investigator_entries": [],
        }))
    as_of = date(2026, 8, 5)
    paths = controller._paths(as_of.isoformat())

    state = controller._start(as_of, paths)

    assert state["phase"] == "REVIEW_REQUIRED"
    response = json.loads(paths["template"].read_text())
    response.update({
        "status": "complete",
        "executive_summary": "The routed signal is already handled.",
        "boundary_assessment": "Product narratives remained local.",
    })
    response["signal_reviews"][0].update({
        "disposition": "already_resolved",
        "classification": "shared_validation",
        "evidence_summary": "The current shared contract addresses this pattern.",
        "rationale": "No additional framework change is supported.",
    })
    paths["response"].write_text(json.dumps(response))

    completed = controller._advance(state, paths)

    assert completed["phase"] == "COMPLETE"
    assert completed["suggestion_count"] == 0
    assert paths["suggestions_json"].exists()

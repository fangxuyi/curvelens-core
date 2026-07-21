import json
from pathlib import Path

import pytest

from ccvm.reference.product import load_product
from ccvm.workflow.finalize import AnalysisValidationError, validate_and_render
from ccvm.workflow.packets import build_analysis_packets
from ccvm.workflow.quality import assess_quality


def _quality(futures_count=12, options_count=100, futures_status="PASS", options_status="PASS"):
    return {
        "overall_status": "PASS",
        "futures": {"status": futures_status, "record_count": futures_count, "notes": []},
        "options": {"status": options_status, "record_count": options_count, "notes": []},
    }


def test_quality_retries_only_missing_market_inputs():
    missing = assess_quality(_quality(futures_count=0, futures_status="INSUFFICIENT_DATA"), 1, 2)
    assert missing["should_retry"] is True
    invalid = assess_quality(_quality(options_status="FAIL"), 1, 2)
    assert invalid["should_retry"] is False
    assert invalid["disposition"] == "READY_WITH_LIMITATIONS"


@pytest.mark.parametrize("product_key", ["gold", "wti"])
def test_profiles_define_three_independent_roles(product_key):
    roles = load_product(product_key).analysis_roles
    assert len(roles) == 3
    assert len({role.key for role in roles}) == 3
    assert all(role.mandate and role.section_keys and role.required_checks for role in roles)


def _packets(tmp_path: Path):
    report = {"sections": {
        "what_changed": {"move": 1}, "market_risk": {"iv": .2},
        "rnd": {"status": "invalid_surface"}, "macro": {"real_yield": 2.0},
        "term_structure": {"slope": -1}, "history_context": {}, "monitor": {},
        "oi": {}, "cot": {},
    }}
    articles = [
        {"title": "Fed and real yields move gold", "text": "Gold reacts to Federal Reserve policy",
         "url": "https://example.test/a", "published_at": "2026-07-20", "source_name": "Test"},
        {"title": "Fed and real yields move gold", "text": "duplicate",
         "url": "https://example.test/a", "published_at": "2026-07-20", "source_name": "Test"},
    ]
    return build_analysis_packets(
        product=load_product("gold"), trade_date="2026-07-20", report=report,
        quality=_quality(options_status="WARN"), articles=articles, output_dir=tmp_path,
    )


def test_packets_are_role_scoped_and_news_is_deduplicated(tmp_path):
    manifest = _packets(tmp_path)
    macro = json.loads(Path(manifest["role_packets"]["macro"]).read_text())
    assert set(macro["computed_sections"]) == {"macro", "cot", "what_changed"}
    assert len(macro["relevant_news"]) == 1
    assert macro["relevant_news"][0]["article_id"].startswith("news:")


def test_packet_id_is_stable(tmp_path):
    first = _packets(tmp_path / "a")["packet_id"]
    second = _packets(tmp_path / "b")["packet_id"]
    assert first == second


def test_finalizer_requires_all_roles_and_known_evidence(tmp_path):
    manifest = _packets(tmp_path / "packets")
    for role in manifest["roles"]:
        path = Path(manifest["role_response_templates"][role])
        response = json.loads(path.read_text())
        response["status"] = "limited"
        response["data_quality_assessment"] = "Options are limited."
        packet = json.loads(Path(manifest["role_packets"][role]).read_text())
        evidence_id = next(iter(packet["computed_sections"].values()))["evidence_id"]
        response["evidence_ids"] = [evidence_id]
        response["data_findings"] = [{"claim": "A limited test finding.", "evidence_ids": [evidence_id]}]
        response["forward_view"]["thesis"] = "The evidence remains limited."
        path.write_text(json.dumps(response))
    synthesis_path = Path(manifest["synthesis_response_template"])
    synthesis = json.loads(synthesis_path.read_text())
    synthesis.update({"status": "limited", "headline": "Test", "executive_summary": "Test",
                      "evidence_ids": [next(iter(manifest["evidence_registry"]))]})
    synthesis_path.write_text(json.dumps(synthesis))
    json_path, md_path = validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "out")
    assert json_path.exists() and md_path.exists()
    assert json.loads(json_path.read_text())["shadow_mode"] is True

    bad_path = Path(manifest["role_response_templates"][manifest["roles"][0]])
    bad = json.loads(bad_path.read_text())
    bad["evidence_ids"] = ["feature:not-in-packet"]
    bad_path.write_text(json.dumps(bad))
    with pytest.raises(AnalysisValidationError, match="unknown evidence"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad")


def test_shadow_workflow_has_no_direct_model_client_calls():
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "agent" / "run_analysis_workflow.py",
        root / "agent" / "finalize_analysis.py",
        root / "ccvm" / "src" / "ccvm" / "workflow" / "packets.py",
    ]
    prohibited = ("import openai", "import anthropic", "extract_catalysts", '"claude"')
    for path in files:
        text = path.read_text().lower()
        assert not any(term in text for term in prohibited)

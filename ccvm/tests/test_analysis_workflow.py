import json
import os
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from ccvm.reference.product import AnalysisRoleSpec, load_product
from ccvm.workflow.finalize import AnalysisValidationError, validate_and_render
from ccvm.workflow.orchestration import advance_state, initialize_state, next_actions
from ccvm.workflow.packets import build_analysis_packets
from ccvm.workflow.quality import assess_quality


def _quality(futures_count=12, options_count=100, futures_status="PASS", options_status="PASS"):
    return {
        "overall_status": "PASS",
        "futures": {"status": futures_status, "record_count": futures_count, "notes": []},
        "options": {"status": options_status, "record_count": options_count, "notes": []},
    }


def _metrics(evidence_id, count=5):
    return [
        {"label": f"Metric {index}", "value": f"{index}.0%", "comparison": "prior 0.5%",
         "plain_english_meaning": "This is a concrete test measure.",
         "evidence_ids": [evidence_id]}
        for index in range(1, count + 1)
    ]


def test_quality_retries_only_missing_market_inputs():
    missing = assess_quality(_quality(futures_count=0, futures_status="INSUFFICIENT_DATA"), 1, 2)
    assert missing["should_retry"] is True
    invalid = assess_quality(_quality(options_status="FAIL"), 1, 2)
    assert invalid["should_retry"] is False
    assert invalid["disposition"] == "READY_WITH_LIMITATIONS"


def test_quality_policy_is_profile_driven_not_product_named():
    report = {"curves": {"status": "INSUFFICIENT_DATA", "record_count": 0, "notes": []}}
    result = assess_quality(
        report, 1, 1, blocking_sections=("curves",),
        retryable_empty_sections=("curves",),
    )
    assert result["disposition"] == "BLOCKED"
    assert result["blocked_sections"] == ["curves"]


@pytest.mark.parametrize("product_key", ["gold", "wti"])
def test_profiles_define_three_independent_roles(product_key):
    roles = load_product(product_key).analysis_roles
    assert len(roles) == 3
    assert len({role.key for role in roles}) == 3
    assert all(
        role.mandate and role.section_keys and role.required_checks
        and role.report_requirements and role.minimum_key_metrics >= 4
        for role in roles
    )


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


def test_wti_packets_use_the_same_workflow_with_fundamentals_desk(tmp_path):
    product = load_product("wti")
    section_keys = {
        key for role in product.analysis_roles for key in role.section_keys
    }
    report = {"sections": {key: {"status": "available"} for key in section_keys}}
    manifest = build_analysis_packets(
        product=product,
        trade_date="2026-07-20",
        report=report,
        quality=_quality(),
        articles=[{
            "title": "WTI inventories and refinery demand",
            "text": "Cushing stockpiles and refinery runs changed.",
            "url": "https://example.test/wti",
            "published_at": "2026-07-20",
            "source_name": "Test",
        }],
        output_dir=tmp_path,
    )

    assert manifest["roles"] == ["futures_curve", "vol_surface", "fundamentals"]
    fundamentals = json.loads(Path(manifest["role_packets"]["fundamentals"]).read_text())
    assert set(fundamentals["computed_sections"]) == {
        "fundamentals", "eia_seasonal", "agreement", "scenarios",
    }
    assert fundamentals["relevant_news"][0]["article_id"].startswith("news:")


def test_packet_id_is_stable(tmp_path):
    first = _packets(tmp_path / "a")["packet_id"]
    second = _packets(tmp_path / "b")["packet_id"]
    assert first == second


def test_packet_builder_supports_arbitrary_configured_roles(tmp_path):
    base = load_product("gold")
    roles = tuple(
        AnalysisRoleSpec(
            key=f"desk_{index}", display_name=f"Desk {index}", mandate="Analyze evidence.",
            section_keys=("market_risk",), news_keywords=("gold",),
            required_checks=("Check the evidence.",),
        ) for index in range(5)
    )
    product = replace(base, key="synthetic", analysis_roles=roles)
    manifest = build_analysis_packets(
        product=product, trade_date="2026-07-20",
        report={"sections": {"market_risk": {"status": "available"}}},
        quality=_quality(), articles=[], output_dir=tmp_path,
    )
    assert manifest["roles"] == [f"desk_{index}" for index in range(5)]
    assert len(manifest["role_response_paths"]) == 5
    assert all(not Path(path).exists() for path in manifest["role_response_paths"].values())


def test_finalizer_requires_all_roles_and_known_evidence(tmp_path):
    manifest = _packets(tmp_path / "packets")
    for role in manifest["roles"]:
        template = Path(manifest["role_response_templates"][role])
        path = Path(manifest["role_response_paths"][role])
        response = json.loads(template.read_text())
        response["status"] = "limited"
        response["data_quality_assessment"] = "Options are limited."
        packet = json.loads(Path(manifest["role_packets"][role]).read_text())
        evidence_id = next(iter(packet["computed_sections"].values()))["evidence_id"]
        response["evidence_ids"] = [evidence_id]
        response["key_metrics"] = _metrics(evidence_id)
        response["data_findings"] = [{"claim": "A limited test finding.", "evidence_ids": [evidence_id]}]
        response["forward_view"].update({
            "horizon": "1m", "bias": "neutral", "thesis": "The evidence remains limited."
        })
        response["required_check_results"] = [
            {"check": check, "status": "concern", "evidence_ids": [evidence_id]}
            for check in packet["required_checks"]
        ]
        path.write_text(json.dumps(response))
    synthesis_template = Path(manifest["synthesis_response_template"])
    synthesis_path = Path(manifest["synthesis_response_path"])
    synthesis = json.loads(synthesis_template.read_text())
    used_id = json.loads(
        Path(manifest["role_response_paths"][manifest["roles"][0]]).read_text()
    )["evidence_ids"][0]
    synthesis.update({"status": "limited", "headline": "Test", "executive_summary": "Test",
                      "plain_english_summary": "The test signals are mixed.",
                      "market_snapshot": _metrics(used_id, 6),
                      "data_limitations": ["Specialists were limited."],
                      "evidence_ids": [used_id]})
    synthesis_path.write_text(json.dumps(synthesis))
    with pytest.raises(AnalysisValidationError, match="forward view requires horizon"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "incomplete")
    synthesis.update({"status": "limited", "headline": "Test", "executive_summary": "Test",
                      "plain_english_summary": "The test signals are mixed.",
                      "market_snapshot": _metrics(used_id, 6),
                      "overall_forward_view": {"horizon": "1m", "bias": "neutral", "thesis": "Mixed."},
                      "data_limitations": ["Specialists were limited."],
                      "evidence_ids": [used_id]})
    synthesis_path.write_text(json.dumps(synthesis))
    json_path, md_path = validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "out")
    assert json_path.exists() and md_path.exists()
    output = json.loads(json_path.read_text())
    assert output["workflow_mode"] == "agent_orchestrated"
    assert output["delivery_approved"] is False
    markdown = md_path.read_text()
    assert "Overall forward view" in markdown and "Data limitations" in markdown

    bad_path = Path(manifest["role_response_paths"][manifest["roles"][0]])
    bad = json.loads(bad_path.read_text())
    bad["evidence_ids"] = ["feature:not-in-packet"]
    bad_path.write_text(json.dumps(bad))
    with pytest.raises(AnalysisValidationError, match="unknown evidence"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad")


def _write_valid_role(manifest, role):
    template = json.loads(Path(manifest["role_response_templates"][role]).read_text())
    packet = json.loads(Path(manifest["role_packets"][role]).read_text())
    evidence_id = next(iter(packet["computed_sections"].values()))["evidence_id"]
    template.update({
        "status": "limited", "data_quality_assessment": "Reviewed with limitations.",
        "key_metrics": _metrics(evidence_id),
        "data_findings": [{"claim": "Observed evidence.", "evidence_ids": [evidence_id]}],
        "forward_view": {"horizon": "1m", "bias": "neutral", "thesis": "Evidence is mixed.",
                         "confirmations": [], "invalidations": []},
        "evidence_ids": [evidence_id],
        "required_check_results": [
            {"check": check, "status": "concern", "evidence_ids": [evidence_id]}
            for check in packet["required_checks"]
        ],
    })
    Path(manifest["role_response_paths"][role]).write_text(json.dumps(template))


def test_generic_orchestration_gates_qc_roles_and_synthesis(tmp_path):
    manifest = _packets(tmp_path / "run")
    manifest_path = tmp_path / "run" / "manifest.json"
    state_path, state = initialize_state(
        manifest_path=manifest_path, quality=_quality(), quality_attempts=[],
        repo_root=Path(__file__).resolve().parents[2],
    )
    assert [a["action"] for a in next_actions(state)] == ["RUN_QC_REVIEWER"]
    qc_template = json.loads(Path(state["qc"]["template_path"]).read_text())
    qc_template.update({"disposition": "accept", "rationale": "Inputs are usable."})
    Path(state["qc"]["response_path"]).write_text(json.dumps(qc_template))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "SPECIALISTS_REQUIRED"
    assert {a["role"] for a in next_actions(state)} == set(manifest["roles"])
    task_text = Path(next_actions(state)[0]["task_path"]).read_text()
    assert "Fed and real yields move gold" not in task_text

    for role in reversed(manifest["roles"]):
        _write_valid_role(manifest, role)
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "SYNTHESIS_REQUIRED"
    synthesis = json.loads(Path(manifest["synthesis_response_template"]).read_text())
    used = json.loads(Path(manifest["role_response_paths"][manifest["roles"][0]]).read_text())["evidence_ids"][0]
    synthesis.update({"status": "limited", "headline": "Mixed setup",
                      "executive_summary": "Specialists identify a mixed setup.",
                      "plain_english_summary": "The market signals are mixed today.",
                      "market_snapshot": _metrics(used, 6),
                      "overall_forward_view": {"horizon": "1m", "bias": "neutral", "thesis": "Signals are mixed."},
                      "data_limitations": ["Synthetic evidence is limited."],
                      "evidence_ids": [used]})
    Path(manifest["synthesis_response_path"]).write_text(json.dumps(synthesis))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "READY_TO_FINALIZE"


def test_synthesis_does_not_exist_before_specialists_validate(tmp_path):
    manifest = _packets(tmp_path / "run")
    state_path, state = initialize_state(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
    )
    assert not (tmp_path / "run" / "synthesis.task.md").exists()
    assert state["phase"] == "QC_REVIEW_REQUIRED"


def test_only_invalid_specialist_is_retried(tmp_path):
    manifest = _packets(tmp_path / "run")
    state_path, state = initialize_state(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
    )
    qc = json.loads(Path(state["qc"]["template_path"]).read_text())
    qc.update({"disposition": "accept", "rationale": "Usable."})
    Path(state["qc"]["response_path"]).write_text(json.dumps(qc))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    for role in manifest["roles"]:
        _write_valid_role(manifest, role)
    bad_role = manifest["roles"][1]
    bad_path = Path(manifest["role_response_paths"][bad_role])
    bad = json.loads(bad_path.read_text())
    bad["packet_id"] = "stale"
    bad_path.write_text(json.dumps(bad))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    actions = next_actions(state)
    assert [action["role"] for action in actions] == [bad_role]
    assert "does not match" in Path(actions[0]["task_path"]).read_text()
    bad_path.write_text(json.dumps(bad))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "BLOCKED"


def test_qc_retry_must_be_allowlisted(tmp_path):
    _packets(tmp_path / "run")
    state_path, state = initialize_state(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(futures_count=0),
        quality_attempts=[{"retry_sections": ["futures"]}],
        repo_root=Path(__file__).resolve().parents[2],
    )
    qc = json.loads(Path(state["qc"]["template_path"]).read_text())
    qc.update({"disposition": "retry", "rationale": "Try collection again.",
               "remediation_ids": ["arbitrary_shell"]})
    Path(state["qc"]["response_path"]).write_text(json.dumps(qc))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "QC_REVIEW_REQUIRED"
    assert "non-allowlisted" in state["qc"]["last_error"]


def test_malformed_agent_containers_enter_correction_path(tmp_path):
    manifest = _packets(tmp_path / "run")
    state_path, state = initialize_state(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
    )
    qc = json.loads(Path(state["qc"]["template_path"]).read_text())
    qc.update({"disposition": "accept", "rationale": "Usable."})
    Path(state["qc"]["response_path"]).write_text(json.dumps(qc))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    role = manifest["roles"][0]
    malformed = json.loads(Path(manifest["role_response_templates"][role]).read_text())
    malformed.update({"status": "limited", "data_quality_assessment": "Reviewed.",
                      "forward_view": "not-an-object", "required_check_results": ["bad"]})
    Path(manifest["role_response_paths"][role]).write_text(json.dumps(malformed))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "SPECIALISTS_REQUIRED"
    assert state["roles"][role]["corrections"] == 1


def test_article_content_change_changes_packet_identity(tmp_path):
    product = load_product("gold")
    kwargs = dict(product=product, trade_date="2026-07-20",
                  report={"sections": {"market_risk": {}}}, quality=_quality())
    first = build_analysis_packets(
        **kwargs, articles=[{"title": "Gold", "text": "first", "url": "https://x.test"}],
        output_dir=tmp_path / "a",
    )
    second = build_analysis_packets(
        **kwargs, articles=[{"title": "Gold", "text": "corrected", "url": "https://x.test"}],
        output_dir=tmp_path / "b",
    )
    assert first["packet_id"] != second["packet_id"]


def test_manifest_tampering_is_detected_by_durable_state(tmp_path):
    _packets(tmp_path / "run")
    manifest_path = tmp_path / "run" / "manifest.json"
    state_path, _ = initialize_state(
        manifest_path=manifest_path, quality=_quality(), quality_attempts=[],
        repo_root=Path(__file__).resolve().parents[2],
    )
    manifest = json.loads(manifest_path.read_text())
    manifest["generated_at"] = "tampered"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(AnalysisValidationError, match="manifest content hash changed"):
        advance_state(state_path, Path(__file__).resolve().parents[2])


def test_orchestration_init_is_idempotent_for_completed_packet(tmp_path):
    _packets(tmp_path / "run")
    kwargs = dict(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
    )
    state_path, state = initialize_state(**kwargs)
    state["phase"] = "COMPLETE"
    from ccvm.workflow.orchestration import save_state
    save_state(state_path, state)
    _, resumed = initialize_state(**kwargs)
    assert resumed["phase"] == "COMPLETE"


def test_project_skill_and_custom_agents_are_generic():
    root = Path(__file__).resolve().parents[2]
    skill = root / ".agents" / "skills" / "curvelens-daily-analysis" / "SKILL.md"
    text = skill.read_text()
    assert "analysis_orchestrator.py" in text
    assert "RUN_SPECIALIST" in text and "native subagents" in text
    for name in ("curvelens_data_qc", "curvelens_specialist", "curvelens_synthesizer"):
        config = tomllib.loads((root / ".codex" / "agents" / f"{name}.toml").read_text())
        assert config["name"] == name
        assert config["description"] and config["developer_instructions"]
        assert "model" not in config


def test_orchestrator_cli_reports_persisted_next_action(tmp_path):
    root = Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "analysis_workflow" / "trade_date=2026-07-20"
    _packets(run_dir)
    initialize_state(
        manifest_path=run_dir / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=root,
    )
    env = os.environ.copy()
    env.update({"CCVM_PRODUCT": "gold", "CCVM_DATA_DIR": str(tmp_path)})
    proc = subprocess.run(
        [sys.executable, str(root / "agent" / "analysis_orchestrator.py"),
         "status", "--date", "2026-07-20"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0
    output = json.loads(proc.stdout)
    assert output["phase"] == "QC_REVIEW_REQUIRED"
    assert output["actions"][0]["agent_type"] == "curvelens_data_qc"


def test_agent_workflow_has_no_direct_model_client_calls():
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "agent" / "run_analysis_workflow.py",
        root / "agent" / "finalize_analysis.py",
        root / "ccvm" / "src" / "ccvm" / "workflow" / "packets.py",
        root / "ccvm" / "src" / "ccvm" / "workflow" / "orchestration.py",
        root / "agent" / "analysis_orchestrator.py",
    ]
    prohibited = ("import openai", "import anthropic", "extract_catalysts", '"claude"')
    for path in files:
        text = path.read_text().lower()
        assert not any(term in text for term in prohibited)


def test_obsolete_script_only_daily_entry_point_is_removed():
    root = Path(__file__).resolve().parents[2]
    assert not (root / "agent" / "run_pipeline.py").exists()

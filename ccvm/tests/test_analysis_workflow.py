import json
import os
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from ccvm.reference.product import AnalysisRoleSpec, load_product
from ccvm.workflow.finalize import (
    AnalysisValidationError, validate_and_render, validate_research_plan,
    validate_role_response,
)
from ccvm.workflow.monitoring import build_monitor
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


def _top_views(manifest):
    views = []
    for rank, role in enumerate(manifest["roles"], start=1):
        response = json.loads(Path(manifest["role_response_paths"][role]).read_text())
        evidence_id = response["evidence_ids"][0]
        views.append({
            "rank": rank, "title": f"View from {role}",
            "plain_english_view": "This is a ranked market condition with concrete support.",
            "horizon": "next 1-5 sessions", "confidence": "low",
            "evidence_relationship": "single_desk", "specialist_roles": [role],
            "key_metrics": response["key_metrics"][:2],
            "supporting_evidence": [{"claim": "The desk evidence supports this view.",
                                     "evidence_ids": [evidence_id]}],
            "conflicting_evidence": [],
            "driver_analysis": {
                "status": "partially_supported",
                "explanation": "The validated desk evidence is consistent with the move.",
                "evidence_ids": [evidence_id],
            },
            "story_chain": {
                "observed_move": {
                    "claim": "The validated settlement moved enough to matter today.",
                    "evidence_ids": [evidence_id],
                },
                "narrative_change": {
                    "status": "partially_supported",
                    "claim": "The desk evidence gives a partial narrative for the move.",
                    "evidence_ids": [evidence_id],
                },
                "option_market_readthrough": {
                    "status": "mixed",
                    "claim": "The option read-through is mixed against the settled move.",
                    "evidence_ids": [evidence_id],
                },
                "forward_watch": {
                    "claim": "Watch the next validated settlement and options update.",
                    "evidence_ids": [evidence_id],
                },
            },
            "what_to_watch": ["Watch the next validated settlement and options update."],
        })
    return views


def _synthesis_ids(manifest):
    return sorted({
        evidence_id
        for role in manifest["roles"]
        for evidence_id in json.loads(
            Path(manifest["role_response_paths"][role]).read_text()
        )["evidence_ids"]
    })


def _forecast_ledger(manifest):
    contract = manifest["synthesis_contract"]["forecast_contract"]
    items = []
    for rank, dimension in enumerate(contract["required_dimensions"], start=1):
        response = json.loads(
            Path(manifest["role_response_paths"][manifest["roles"][rank - 1]]).read_text()
        )
        horizon = contract["horizons_sessions"][0]
        items.append({
            "forecast_id": (
                f"{manifest['packet_id'][:16]}:v{rank}:{dimension}:h{horizon}"
            ),
            "source_view_rank": rank,
            "dimension": dimension,
            "metric_key": contract["dimensions"][dimension]["metric_key"],
            "horizon_sessions": horizon,
            "expected_label": contract["dimensions"][dimension]["labels"][0],
            "confidence": "low",
            "evidence_ids": [response["evidence_ids"][0]],
        })
    return items


def _mobile_selection(manifest, selected=(1,)):
    dimensions = manifest["synthesis_contract"]["forecast_contract"]["required_dimensions"]
    candidates = []
    for rank, dimension in enumerate(dimensions, start=1):
        response = json.loads(
            Path(manifest["role_response_paths"][manifest["roles"][rank - 1]]).read_text()
        )
        candidates.append({
            "source_view_rank": rank,
            "disposition": "selected" if rank in selected else "omitted",
            "materiality": "high" if rank == selected[0] else "medium",
            "expected_impact_dimensions": [dimension],
            "rationale": "Current validated evidence determines mobile priority.",
            "evidence_ids": [response["evidence_ids"][0]],
        })
    return {
        "selected_view_ranks": list(selected),
        "selection_rationale": "Only independently material views receive mobile space.",
        "candidates": candidates,
        "limitation_disposition": "included",
        "limitation_rationale": "The limited synthesis requires its data note on mobile.",
    }


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


@pytest.mark.parametrize("product_key", ["gold", "wti", "corn", "silver"])
def test_profiles_define_three_independent_roles(product_key):
    roles = load_product(product_key).analysis_roles
    assert len(roles) == 3
    assert len({role.key for role in roles}) == 3
    assert all(
        role.mandate and role.section_keys and role.required_checks
        and role.report_requirements and role.minimum_key_metrics >= 4
        for role in roles
    )


def _learning_snapshot(status="active"):
    return {
        "schema_version": 2, "as_of": "2026-07-20", "memory_sha256": "a" * 64,
        "advisories": [{
            "advisory_id": "learning:abc123", "status": status,
            "scope": {"dimension": "price_direction", "horizon_sessions": 1,
                      "confidence": "high"},
            "observation": "Historical hit rate was 60% across twenty forecasts.",
            "suggested_adjustment": "Keep confidence bounded by current evidence.",
            "sample_size": 20, "hits": 12, "hit_rate": 0.6, "mean_brier": 0.22,
            "promotion_eligible": True, "source_evaluation_sha256": ["b" * 64],
            "created_at": "2026-07-19T12:00:00+00:00",
            "updated_at": "2026-07-19T12:00:00+00:00",
        }],
        "mobile_advisories": [{
            "advisory_id": "mobile-learning:abc123", "status": status,
            "scope": {
                "source_view_rank": 1, "expected_materiality": "high",
                "impact_dimensions": ["price_direction"],
            },
            "recommendation": "prefer_select",
            "observation": "Rank-one price views were materially relevant in 70% of samples.",
            "suggested_adjustment": "Prefer selection when current evidence matches this scope.",
            "sample_size": 20, "material_count": 14, "material_rate": 0.7,
            "selection_accuracy": 0.65, "missed_material_rate": 0.1,
            "false_prominence_rate": 0.15, "promotion_eligible": True,
            "source_evaluation_sha256": ["c" * 64],
            "created_at": "2026-07-19T12:00:00+00:00",
            "updated_at": "2026-07-19T12:00:00+00:00",
        }],
    }


def _investigator_learning_advisory(status="active", recommendation="prefer_dispatch"):
    return {
        "advisory_id": "investigator-learning:abc123",
        "status": status,
        "scope": {
            "role": "futures_curve", "horizon_sessions": 1,
            "expected_materiality": "medium",
            "impact_dimensions": ["price_direction"],
        },
        "recommendation": recommendation,
        "observation": "This investigator scope was material in 70% of scored samples.",
        "suggested_adjustment": "Prefer dispatch only when current evidence matches the scope.",
        "sample_size": 20, "material_count": 14, "material_rate": 0.7,
        "materiality_hit_rate": 0.65, "lead_use_rate": 0.6,
        "rejected_material_rate": 0.1, "promotion_eligible": True,
        "source_evaluation_sha256": ["d" * 64],
        "created_at": "2026-07-19T12:00:00+00:00",
        "updated_at": "2026-07-19T12:00:00+00:00",
    }


def _packets(tmp_path: Path, learning_snapshot=None):
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
        learning_snapshot=learning_snapshot,
    )


def _write_research_plan(manifest, selected=None):
    selected = list(manifest["roles"] if selected is None else selected)
    template = json.loads(Path(manifest["research_plan_template"]).read_text())
    investigations = []
    evidence_ids = []
    for role in selected:
        packet = json.loads(Path(manifest["role_packets"][role]).read_text())
        evidence_id = next(iter(packet["computed_sections"].values()))["evidence_id"]
        evidence_ids.append(evidence_id)
        investigations.append({
            "investigation_id": f"{manifest['packet_id'][:16]}:{role}",
            "role": role,
            "question": f"What could the {role} evidence change in today's ranked view?",
            "rationale": "The cited anomaly could materially alter the lead conclusion.",
            "priority": "high",
            "expected_materiality": "medium",
            "horizon_sessions": 1,
            "expected_impact_dimensions": ["price_direction"],
            "evidence_ids": [evidence_id],
        })
    template.update({
        "status": "complete",
        "market_scan_summary": "The complete evidence scan found bounded questions.",
        "investigations": investigations,
        "omitted_roles": [
            {"role": role, "rationale": "No additional decision-relevant question today."}
            for role in manifest["roles"] if role not in selected
        ],
        "evidence_ids": sorted(set(evidence_ids)),
    })
    Path(manifest["research_plan_response_path"]).write_text(json.dumps(template))
    return template


def _investigator_feedback(manifest):
    plan = json.loads(Path(manifest["research_plan_response_path"]).read_text())
    return [{
        "investigation_id": item["investigation_id"],
        "disposition": "used",
        "rationale": "The lead retained one evidence-backed candidate finding.",
        "used_finding_ids": [f"{item['investigation_id']}:f1"],
        "evidence_ids": item["evidence_ids"],
    } for item in plan["investigations"]]


def test_packets_are_role_scoped_and_news_is_deduplicated(tmp_path):
    manifest = _packets(tmp_path)
    macro = json.loads(Path(manifest["role_packets"]["macro"]).read_text())
    assert set(macro["computed_sections"]) == {"macro", "cot", "what_changed"}
    assert len(macro["relevant_news"]) == 1
    assert macro["relevant_news"][0]["article_id"].startswith("news:")
    canonical = json.loads(Path(manifest["canonical_packet"]).read_text())
    assert set(canonical["computed_sections"]) > set(macro["computed_sections"])
    assert canonical["evidence_registry"] == manifest["evidence_registry"]


def test_synthesis_template_exposes_complete_ranked_top_view_shape(tmp_path):
    manifest = _packets(tmp_path)
    template = json.loads(Path(manifest["synthesis_response_template"]).read_text())
    assert [view["rank"] for view in template["top_views"]] == [1, 2, 3]
    assert all({
        "title", "plain_english_view", "horizon", "confidence",
        "evidence_relationship", "specialist_roles", "key_metrics",
        "supporting_evidence", "conflicting_evidence", "driver_analysis",
        "story_chain", "what_to_watch",
    }.issubset(view) for view in template["top_views"])
    assert all(
        set(view["story_chain"]) == {
            "observed_move", "narrative_change", "option_market_readthrough",
            "forward_watch",
        }
        for view in template["top_views"]
    )
    metric_fields = {
        "label", "value", "comparison", "plain_english_meaning", "evidence_ids",
    }
    assert all(
        len(view["key_metrics"]) == 2
        and all(set(metric) == metric_fields for metric in view["key_metrics"])
        for view in template["top_views"]
    )
    assert len(template["market_snapshot"]) == 6
    assert all(set(metric) == metric_fields for metric in template["market_snapshot"])
    assert set(template["investigator_feedback"][0]) == {
        "investigation_id", "disposition", "rationale",
        "used_finding_ids", "evidence_ids",
    }
    assert [item["source_view_rank"] for item in template["forecast_ledger"]] == [1, 2, 3]
    assert template["mobile_selection"]["selected_view_ranks"] == [1]
    assert [item["source_view_rank"] for item in template["mobile_selection"]["candidates"]] == [1, 2, 3]
    assert set(manifest["synthesis_contract"]["forecast_contract"]["required_dimensions"]) == {
        "price_direction", "volatility_direction", "market_impact",
    }
    assert manifest["synthesis_contract"]["mobile_relevance_contract"]["horizon_sessions"] == 1
    role_contract = manifest["synthesis_contract"]["reporting"]["specialist_role_keys"]
    assert role_contract["configured_role_keys"] == manifest["roles"]
    assert role_contract["display_names"] == {
        role: manifest["investigator_capabilities"][role]["display_name"]
        for role in manifest["roles"]
    }
    assert "never its display_name" in role_contract["rule"]
    assert manifest["synthesis_contract"]["reporting"]["top_view_schema"][
        "specialist_roles"
    ] == ["selected dispatched role key (not display_name)"]
    metric_rule = manifest["synthesis_contract"]["reporting"]["top_view_metric_value_rule"]
    assert "numeric character" in metric_rule
    assert "categorical statuses" in metric_rule
    assert manifest["synthesis_contract"]["reporting"]["top_view_schema"][
        "key_metrics"
    ] == ["2-3 exact metric objects; each value contains a number and evidence_ids"]
    snapshot_rule = manifest["synthesis_contract"]["reporting"][
        "market_snapshot_metric_value_rule"
    ]
    assert "numeric character" in snapshot_rule
    assert "categorical diagnostics" in snapshot_rule
    assert manifest["synthesis_contract"]["reporting"]["market_snapshot_items"] == (
        "6 to 10 exact numeric values cited to canonical evidence; each value contains a "
        "number and an evidence-backed measure"
    )
    assert template["memory_feedback"] == []
    assert template["mobile_memory_feedback"] == []


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


def test_packet_identity_includes_forecast_policy(monkeypatch, tmp_path):
    from ccvm.workflow import packets as packets_module

    first = _packets(tmp_path / "a")["packet_id"]
    rule = packets_module.FORECAST_DIMENSIONS["price_direction"]["outcome_rule"]
    monkeypatch.setitem(rule, "thresholds", [0.01])
    second = _packets(tmp_path / "b")["packet_id"]
    assert first != second


def test_packet_identity_includes_mobile_relevance_policy(monkeypatch, tmp_path):
    from ccvm.workflow import packets as packets_module

    first = _packets(tmp_path / "a")["packet_id"]
    definition = packets_module.MOBILE_RELEVANCE_DIMENSIONS["volatility_direction"]
    monkeypatch.setitem(definition, "thresholds", [0.01, 0.02])
    second = _packets(tmp_path / "b")["packet_id"]
    assert first != second


def test_packet_identity_includes_immutable_learning_snapshot(tmp_path):
    snapshot = _learning_snapshot()
    first = _packets(tmp_path / "a", learning_snapshot=snapshot)["packet_id"]
    changed = json.loads(json.dumps(snapshot))
    changed["advisories"][0]["sample_size"] = 21
    second = _packets(tmp_path / "b", learning_snapshot=changed)["packet_id"]
    assert first != second
    template = json.loads(
        Path(_packets(tmp_path / "c", learning_snapshot=snapshot)[
            "synthesis_response_template"
        ]).read_text()
    )
    assert template["memory_feedback"][0]["advisory_id"] == "learning:abc123"
    assert template["mobile_memory_feedback"][0]["advisory_id"] == "mobile-learning:abc123"


def test_shadow_learning_is_visible_but_cannot_be_used(tmp_path):
    manifest = _packets(tmp_path, learning_snapshot=_learning_snapshot("shadow"))
    template = json.loads(Path(manifest["synthesis_response_template"]).read_text())
    assert template["memory_feedback"][0]["disposition"] == (
        "shadow_would_use|shadow_rejected"
    )
    context = manifest["synthesis_contract"]["learning_context"]
    assert context["advisories"][0]["status"] == "shadow"
    assert context["mobile_advisories"][0]["status"] == "shadow"
    assert "must not influence" in context["rule"]
    assert "must not affect" in context["mobile_rule"]


def test_investigator_learning_is_isolated_to_research_planning(tmp_path):
    snapshot = _learning_snapshot()
    snapshot["schema_version"] = 3
    snapshot["investigator_advisories"] = [_investigator_learning_advisory()]
    manifest = _packets(tmp_path, learning_snapshot=snapshot)
    plan = _write_research_plan(manifest)
    plan["investigator_memory_feedback"][0].update({
        "disposition": "used",
        "rationale": "Current canonical evidence matches the active dispatch scope.",
    })
    Path(manifest["research_plan_response_path"]).write_text(json.dumps(plan))
    validated = validate_research_plan(tmp_path / "manifest.json")
    assert validated["investigator_memory_feedback"][0]["disposition"] == "used"
    assert manifest["research_contract"]["learning_context"][
        "investigator_advisories"
    ][0]["status"] == "active"
    assert "investigator_advisories" not in manifest[
        "synthesis_contract"
    ]["learning_context"]


def test_shadow_investigator_advice_cannot_be_marked_used(tmp_path):
    snapshot = _learning_snapshot()
    snapshot["schema_version"] = 3
    snapshot["investigator_advisories"] = [
        _investigator_learning_advisory(status="shadow")
    ]
    manifest = _packets(tmp_path, learning_snapshot=snapshot)
    plan = _write_research_plan(manifest)
    plan["investigator_memory_feedback"][0].update({
        "disposition": "used", "rationale": "This must remain counterfactual.",
    })
    Path(manifest["research_plan_response_path"]).write_text(json.dumps(plan))
    with pytest.raises(AnalysisValidationError, match="shadow isolation"):
        validate_research_plan(tmp_path / "manifest.json")


def test_stale_investigator_advice_is_not_routed_to_planner(tmp_path):
    snapshot = _learning_snapshot()
    snapshot["schema_version"] = 3
    advisory = _investigator_learning_advisory()
    advisory["scope"]["role"] = "removed_capability"
    snapshot["investigator_advisories"] = [advisory]
    manifest = _packets(tmp_path, learning_snapshot=snapshot)
    assert manifest["research_contract"]["learning_context"][
        "investigator_advisories"
    ] == []


def test_active_investigator_skip_advice_must_be_applied_when_used(tmp_path):
    snapshot = _learning_snapshot()
    snapshot["schema_version"] = 3
    snapshot["investigator_advisories"] = [
        _investigator_learning_advisory(recommendation="prefer_skip")
    ]
    manifest = _packets(tmp_path, learning_snapshot=snapshot)
    plan = _write_research_plan(manifest)
    plan["investigator_memory_feedback"][0].update({
        "disposition": "used", "rationale": "Applying the active skip advice.",
    })
    Path(manifest["research_plan_response_path"]).write_text(json.dumps(plan))
    with pytest.raises(AnalysisValidationError, match="prefer_skip advice"):
        validate_research_plan(tmp_path / "manifest.json")


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


def test_investigator_dimension_error_lists_sorted_outcome_enum(tmp_path):
    manifest = _packets(tmp_path / "run")
    role = manifest["roles"][0]
    _write_research_plan(manifest, selected=[role])
    _write_valid_role(manifest, role)
    response_path = Path(manifest["role_response_paths"][role])
    response = json.loads(response_path.read_text())
    response["candidate_findings"][0]["expected_impact_dimensions"] = [
        "curve", "positioning",
    ]
    response_path.write_text(json.dumps(response))

    with pytest.raises(AnalysisValidationError) as exc_info:
        validate_role_response(tmp_path / "run" / "manifest.json", role)

    assert "alphabetically sorted subset" in str(exc_info.value)
    assert "['market_impact', 'price_direction', 'volatility_direction']" in str(
        exc_info.value
    )


def test_investigator_finding_citations_must_be_disjoint(tmp_path):
    manifest = _packets(tmp_path / "run")
    role = manifest["roles"][0]
    _write_research_plan(manifest, selected=[role])
    _write_valid_role(manifest, role)
    response_path = Path(manifest["role_response_paths"][role])
    response = json.loads(response_path.read_text())
    support_id = response["candidate_findings"][0]["evidence_ids"][0]
    other_ids = set(manifest["evidence_registry"]) - {support_id}
    assert other_ids
    other_id = sorted(other_ids)[0]
    response["candidate_findings"][0]["counterevidence_ids"] = [other_id]
    response["evidence_ids"].append(other_id)
    response_path.write_text(json.dumps(response))

    assert validate_role_response(tmp_path / "run" / "manifest.json", role)

    response["candidate_findings"][0]["counterevidence_ids"] = [support_id]
    response_path.write_text(json.dumps(response))
    with pytest.raises(
        AnalysisValidationError,
        match="cannot use one ID as support and counterevidence",
    ):
        validate_role_response(tmp_path / "run" / "manifest.json", role)


def test_finalizer_requires_all_roles_and_known_evidence(tmp_path):
    manifest = _packets(tmp_path / "packets", learning_snapshot=_learning_snapshot())
    plan = _write_research_plan(manifest)
    for role in manifest["roles"]:
        template = Path(manifest["role_response_templates"][role])
        path = Path(manifest["role_response_paths"][role])
        response = json.loads(template.read_text())
        response["status"] = "limited"
        investigation = next(item for item in plan["investigations"] if item["role"] == role)
        response["question"] = investigation["question"]
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
        response["candidate_findings"][0].update({
            "claim": "The targeted evidence may affect the ranked view.",
            "materiality": "medium", "horizon_sessions": 1, "confidence": "low",
            "expected_impact_dimensions": ["price_direction"],
            "evidence_ids": [evidence_id], "counterevidence_ids": [],
            "confirmations": ["Watch the next validated settlement."],
            "invalidations": ["Invalidate if the cited condition reverses."],
            "unresolved_question": "",
        })
        path.write_text(json.dumps(response))
    synthesis_template = Path(manifest["synthesis_response_template"])
    synthesis_path = Path(manifest["synthesis_response_path"])
    synthesis = json.loads(synthesis_template.read_text())
    synthesis["memory_feedback"][0].update({
        "disposition": "used",
        "rationale": "Current validated evidence supports retaining the bounded reminder.",
    })
    synthesis["mobile_memory_feedback"][0].update({
        "disposition": "used",
        "rationale": "Current evidence matches the active mobile materiality scope.",
    })
    used_id = json.loads(
        Path(manifest["role_response_paths"][manifest["roles"][0]]).read_text()
    )["evidence_ids"][0]
    synthesis.update({"status": "limited", "headline": "Test", "executive_summary": "Test",
                      "plain_english_summary": "The test signals are mixed.",
                      "market_snapshot": _metrics(used_id, 6),
                      "top_views": _top_views(manifest),
                      "mobile_selection": _mobile_selection(manifest),
                      "forecast_ledger": _forecast_ledger(manifest),
                      "investigator_feedback": _investigator_feedback(manifest),
                      "data_limitations": ["Specialists were limited."],
                      "evidence_ids": _synthesis_ids(manifest)})
    direct_id = "feature:rnd:2026-07-20"
    assert direct_id not in _synthesis_ids(manifest)
    synthesis["top_views"][0].update({
        "evidence_relationship": "direct_evidence",
        "specialist_roles": [],
        "key_metrics": _metrics(direct_id, 2),
        "supporting_evidence": [{
            "claim": "Canonical surface evidence remains material even when no worker selected it.",
            "evidence_ids": [direct_id],
        }],
    })
    synthesis["evidence_ids"].append(direct_id)
    synthesis_path.write_text(json.dumps(synthesis))
    with pytest.raises(AnalysisValidationError, match="forward view requires horizon"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "incomplete")
    synthesis.update({"status": "limited", "headline": "Test", "executive_summary": "Test",
                      "plain_english_summary": "The test signals are mixed.",
                      "market_snapshot": _metrics(used_id, 6),
                      "top_views": _top_views(manifest),
                      "mobile_selection": _mobile_selection(manifest),
                      "forecast_ledger": _forecast_ledger(manifest),
                      "overall_forward_view": {"horizon": "1m", "bias": "neutral", "thesis": "Mixed."},
                      "data_limitations": ["Specialists were limited."],
                      "evidence_ids": _synthesis_ids(manifest)})
    synthesis_path.write_text(json.dumps(synthesis))
    json_path, md_path, statistics_path, mobile_path = validate_and_render(
        tmp_path / "packets" / "manifest.json", tmp_path / "out",
    )
    assert all(path.exists() for path in (
        json_path, md_path, statistics_path, mobile_path,
    ))
    output = json.loads(json_path.read_text())
    assert output["workflow_mode"] == "agent_orchestrated"
    assert output["statistics_integrated"] is True
    assert output["delivery_approved"] is False
    assert output["synthesis"]["forecast_ledger"] == _forecast_ledger(manifest)
    assert output["forecast_contract"]["version"] == 2
    assert output["mobile_relevance_contract"]["version"] == 1
    assert output["forecast_contract"]["dimensions"]["volatility_direction"][
        "outcome_rule"
    ]["source_metric"] == "front_atm_iv"
    assert output["synthesis"]["memory_feedback"][0]["advisory_id"] == "learning:abc123"
    markdown = md_path.read_text()
    assert "Overall forward view" in markdown and "Data limitations" in markdown
    assert "Driver and news validation" in markdown
    assert "Daily story chain" in markdown
    assert "Validated statistics" in markdown
    statistics = statistics_path.read_text()
    assert "# GOLD Daily Statistics — 2026-07-20" in statistics
    assert "## Market snapshot" in statistics
    assert "## Futures Curve statistics" in statistics
    assert "## Evidence coverage" in statistics
    assert "Numerical audit supplement" in statistics
    mobile = mobile_path.read_text()
    assert "*GOLD Daily Brief — 2026-07-20*" in mobile
    assert "*1. View from futures_curve*" in mobile
    assert "Why it moved: The desk evidence gives a partial narrative for the move." in mobile
    assert "Options: The option read-through is mixed against the settled move." in mobile
    assert "Key move: Metric 1: 1.0%" in mobile
    assert "View from vol_surface" not in mobile
    assert "forecast_ledger" not in mobile and "memory_feedback" not in mobile
    assert len(mobile) <= 1400

    valid_synthesis = json.loads(json.dumps(synthesis))
    first_role = manifest["roles"][0]
    display_name = manifest["investigator_capabilities"][first_role]["display_name"]
    assert display_name != first_role
    bad_synthesis = json.loads(json.dumps(valid_synthesis))
    bad_synthesis["top_views"][0]["specialist_roles"] = [display_name]
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(
        AnalysisValidationError, match=r"top_views\[0\]\.specialist_roles must name configured roles",
    ):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-display-name")

    bad_synthesis = json.loads(json.dumps(valid_synthesis))
    bad_synthesis["top_views"][0]["specialist_roles"] = ["unselected_role_key"]
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(
        AnalysisValidationError, match=r"top_views\[0\]\.specialist_roles must name configured roles",
    ):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-unselected-key")
    synthesis_path.write_text(json.dumps(valid_synthesis))
    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["top_views"][0]["key_metrics"][0]["value"] = "confirmed"
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(
        AnalysisValidationError,
        match=r"synthesis\.top_views\[0\]\.key_metrics\[0\]\.value must contain a number",
    ):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-categorical-metric")
    synthesis_path.write_text(json.dumps(synthesis))
    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["market_snapshot"][0]["value"] = "invalid_surface"
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(
        AnalysisValidationError,
        match=r"synthesis\.market_snapshot\[0\]\.value must contain a number",
    ):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-categorical-snapshot")
    synthesis_path.write_text(json.dumps(synthesis))

    redundant_mobile = json.loads(json.dumps(synthesis))
    redundant_mobile["mobile_selection"]["candidates"][0][
        "expected_impact_dimensions"
    ] = ["market_impact", "price_direction"]
    synthesis_path.write_text(json.dumps(redundant_mobile))
    normalized_json, *_ = validate_and_render(
        tmp_path / "packets" / "manifest.json", tmp_path / "normalized-mobile-link",
    )
    normalized = json.loads(normalized_json.read_text())
    assert normalized["synthesis"]["mobile_selection"]["candidates"][0][
        "expected_impact_dimensions"
    ] == ["price_direction"]
    synthesis_path.write_text(json.dumps(synthesis))

    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["market_snapshot"][0] = "legacy metric shorthand"
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(
        AnalysisValidationError, match=r"synthesis\.market_snapshot\[0\] must be an object",
    ):
        validate_and_render(
            tmp_path / "packets" / "manifest.json", tmp_path / "bad-market-snapshot",
        )
    synthesis_path.write_text(json.dumps(synthesis))

    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["top_views"][0]["story_chain"]["option_market_readthrough"].update({
        "status": "confirmed", "evidence_ids": [],
    })
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(
        AnalysisValidationError,
        match=r"story_chain\.option_market_readthrough requires evidence",
    ):
        validate_and_render(
            tmp_path / "packets" / "manifest.json", tmp_path / "bad-story-chain",
        )
    synthesis_path.write_text(json.dumps(synthesis))

    bad_synthesis = json.loads(synthesis_path.read_text())
    bad_synthesis["forecast_ledger"][0]["expected_label"] = "certainly_up"
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(AnalysisValidationError, match="expected_label is not configured"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-forecast")

    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["forecast_ledger"][0]["evidence_ids"] = [
        bad_synthesis["forecast_ledger"][1]["evidence_ids"][0]
    ]
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(AnalysisValidationError, match="evidence from its source top view"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-link")

    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["forecast_ledger"] = bad_synthesis["forecast_ledger"][:2]
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(AnalysisValidationError, match="cover every top view"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-coverage")

    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["memory_feedback"][0]["disposition"] = "shadow_would_use"
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(AnalysisValidationError, match="invalid active disposition"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-memory")
    synthesis_path.write_text(json.dumps(synthesis))

    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["mobile_memory_feedback"][0]["disposition"] = "shadow_would_use"
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(AnalysisValidationError, match="invalid active disposition"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "bad-mobile-memory")
    synthesis_path.write_text(json.dumps(synthesis))

    bad_synthesis = json.loads(json.dumps(synthesis))
    bad_synthesis["mobile_selection"]["selected_view_ranks"] = [2]
    bad_synthesis["mobile_selection"]["candidates"][0]["disposition"] = "omitted"
    bad_synthesis["mobile_selection"]["candidates"][1]["disposition"] = "selected"
    synthesis_path.write_text(json.dumps(bad_synthesis))
    with pytest.raises(AnalysisValidationError, match="must apply its prefer_select"):
        validate_and_render(tmp_path / "packets" / "manifest.json", tmp_path / "unused-mobile-advice")
    synthesis_path.write_text(json.dumps(synthesis))

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
    plan = json.loads(Path(manifest["research_plan_response_path"]).read_text())
    investigation = next(item for item in plan["investigations"] if item["role"] == role)
    template.update({
        "status": "limited", "data_quality_assessment": "Reviewed with limitations.",
        "question": investigation["question"],
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
    template["candidate_findings"][0].update({
        "claim": "The targeted evidence may affect the ranked view.",
        "materiality": "medium", "horizon_sessions": 1, "confidence": "low",
        "expected_impact_dimensions": ["price_direction"],
        "evidence_ids": [evidence_id], "counterevidence_ids": [],
        "confirmations": ["Watch the next validated settlement."],
        "invalidations": ["Invalidate if the cited condition reverses."],
        "unresolved_question": "",
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
    assert state["phase"] == "RESEARCH_PLAN_REQUIRED"
    assert next_actions(state)[0]["action"] == "RUN_RESEARCH_PLANNER"
    planner_task = Path(next_actions(state)[0]["task_path"]).read_text()
    assert "['market_impact', 'price_direction', 'volatility_direction']" in planner_task
    assert "realized-outcome dimensions, not report fields" in planner_task
    _write_research_plan(manifest)
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "INVESTIGATORS_REQUIRED"
    assert {a["role"] for a in next_actions(state)} == set(manifest["roles"])
    task_text = Path(next_actions(state)[0]["task_path"]).read_text()
    assert "Fed and real yields move gold" not in task_text
    assert "['market_impact', 'price_direction', 'volatility_direction']" in task_text
    assert "not capability topics such as curve" in task_text
    assert "evidence_ids and counterevidence_ids disjoint" in task_text
    assert "Set counterevidence_ids to [] when no distinct contrary evidence exists." in task_text

    for role in reversed(manifest["roles"]):
        _write_valid_role(manifest, role)
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "SYNTHESIS_REQUIRED"
    synthesis_task = Path(next_actions(state)[0]["task_path"]).read_text()
    assert "copy only the exact `key` values from the selected dispatched-role list" in synthesis_task
    for role in manifest["roles"]:
        display_name = manifest["investigator_capabilities"][role]["display_name"]
        assert f"- key `{role}`; display_name `{display_name}`; response `" in synthesis_task
    assert "Every top_views key_metrics value must contain at least one numeric character" in synthesis_task
    assert "Never put categorical statuses" in synthesis_task
    assert "Every market_snapshot value must contain at least one numeric character" in synthesis_task
    assert "Keep categorical diagnostics" in synthesis_task
    synthesis = json.loads(Path(manifest["synthesis_response_template"]).read_text())
    used = json.loads(Path(manifest["role_response_paths"][manifest["roles"][0]]).read_text())["evidence_ids"][0]
    synthesis.update({"status": "limited", "headline": "Mixed setup",
                      "executive_summary": "Specialists identify a mixed setup.",
                      "plain_english_summary": "The market signals are mixed today.",
                      "market_snapshot": _metrics(used, 6),
                      "top_views": _top_views(manifest),
                      "mobile_selection": _mobile_selection(manifest),
                      "forecast_ledger": _forecast_ledger(manifest),
                      "investigator_feedback": _investigator_feedback(manifest),
                      "overall_forward_view": {"horizon": "1m", "bias": "neutral", "thesis": "Signals are mixed."},
                      "data_limitations": ["Synthetic evidence is limited."],
                      "evidence_ids": _synthesis_ids(manifest)})
    Path(manifest["synthesis_response_path"]).write_text(json.dumps(synthesis))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "READY_TO_FINALIZE"
    monitor = build_monitor(state_path)
    assert monitor["phase"] == "READY_TO_FINALIZE"
    assert {item["name"] for item in monitor["agents"]} >= {
        "data_quality", "research_plan", "futures_curve", "vol_surface", "macro", "synthesis",
    }
    assert any(item["event"] == "agent_dispatched" for item in monitor["events"])
    monitor_md = state_path.with_name("workflow_monitor.md").read_text()
    assert "Exact assigned task" in monitor_md
    assert "Exact submitted response" in monitor_md


def test_research_plan_may_skip_all_investigators(tmp_path):
    manifest = _packets(tmp_path / "run")
    state_path, state = initialize_state(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
    )
    qc = json.loads(Path(state["qc"]["template_path"]).read_text())
    qc.update({"disposition": "accept", "rationale": "Inputs are usable."})
    Path(state["qc"]["response_path"]).write_text(json.dumps(qc))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    _write_research_plan(manifest, selected=[])
    assert validate_research_plan(tmp_path / "run" / "manifest.json")["investigations"] == []
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "SYNTHESIS_REQUIRED"
    assert [item["status"] for item in state["roles"].values()] == [
        "omitted", "omitted", "omitted",
    ]
    assert next_actions(state)[0]["action"] == "RUN_SYNTHESIZER"


def test_research_plan_rejects_unexplained_capability_omission(tmp_path):
    manifest = _packets(tmp_path / "run")
    plan = _write_research_plan(manifest, selected=[manifest["roles"][0]])
    plan["omitted_roles"] = plan["omitted_roles"][:1]
    Path(manifest["research_plan_response_path"]).write_text(json.dumps(plan))
    with pytest.raises(AnalysisValidationError, match="select or explicitly omit every"):
        validate_research_plan(tmp_path / "run" / "manifest.json")


def test_research_plan_dimension_error_lists_sorted_outcome_enum(tmp_path):
    manifest = _packets(tmp_path / "run")
    plan = _write_research_plan(manifest, selected=[manifest["roles"][0]])
    plan["investigations"][0]["expected_impact_dimensions"] = [
        "price_direction", "market_impact",
    ]
    Path(manifest["research_plan_response_path"]).write_text(json.dumps(plan))
    with pytest.raises(AnalysisValidationError) as exc_info:
        validate_research_plan(tmp_path / "run" / "manifest.json")
    assert "alphabetically sorted subset" in str(exc_info.value)
    assert "['market_impact', 'price_direction', 'volatility_direction']" in str(
        exc_info.value
    )


def test_research_plan_retry_refreshes_task_without_a_response(tmp_path):
    manifest = _packets(tmp_path / "run")
    state_path, state = initialize_state(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
    )
    qc = json.loads(Path(state["qc"]["template_path"]).read_text())
    qc.update({"disposition": "accept", "rationale": "Usable."})
    Path(state["qc"]["response_path"]).write_text(json.dumps(qc))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    plan = _write_research_plan(manifest, selected=[manifest["roles"][0]])
    plan["investigations"][0]["expected_impact_dimensions"] = ["watch_items"]
    Path(manifest["research_plan_response_path"]).write_text(json.dumps(plan))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["research_plan"]["status"] == "retry"
    task_path = Path(state["research_plan"]["task_path"])
    task_path.write_text("stale task")

    state = advance_state(state_path, Path(__file__).resolve().parents[2])

    assert state["phase"] == "RESEARCH_PLAN_REQUIRED"
    assert "realized-outcome dimensions, not report fields" in task_path.read_text()


def test_monitor_preserves_rejected_agent_response(tmp_path):
    manifest = _packets(tmp_path / "run")
    state_path, state = initialize_state(
        manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
        quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
    )
    response_path = Path(state["qc"]["response_path"])
    response_path.write_text('{"invalid": true}')
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "QC_REVIEW_REQUIRED"
    archives = list(response_path.parent.glob("qc.attempt-1.response.invalid-attempt-1.json"))
    assert len(archives) == 1
    monitor = build_monitor(state_path)
    rejected = [item for item in monitor["events"] if item["event"] == "validation_rejected"]
    assert rejected and rejected[0]["response_path"] == str(archives[0])


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
        max_agent_corrections=1,
    )
    qc = json.loads(Path(state["qc"]["template_path"]).read_text())
    qc.update({"disposition": "accept", "rationale": "Usable."})
    Path(state["qc"]["response_path"]).write_text(json.dumps(qc))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    _write_research_plan(manifest)
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
    _write_research_plan(manifest, selected=[manifest["roles"][0]])
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    role = manifest["roles"][0]
    malformed = json.loads(Path(manifest["role_response_templates"][role]).read_text())
    malformed.update({"status": "limited", "data_quality_assessment": "Reviewed.",
                      "forward_view": "not-an-object", "required_check_results": ["bad"]})
    Path(manifest["role_response_paths"][role]).write_text(json.dumps(malformed))
    state = advance_state(state_path, Path(__file__).resolve().parents[2])
    assert state["phase"] == "INVESTIGATORS_REQUIRED"
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


def test_canonical_packet_tampering_is_rejected(tmp_path):
    manifest = _packets(tmp_path / "run")
    canonical_path = Path(manifest["canonical_packet"])
    canonical = json.loads(canonical_path.read_text())
    canonical["quality"]["overall_status"] = "tampered"
    canonical_path.write_text(json.dumps(canonical))
    with pytest.raises(AnalysisValidationError, match="canonical evidence packet"):
        initialize_state(
            manifest_path=tmp_path / "run" / "manifest.json", quality=_quality(),
            quality_attempts=[], repo_root=Path(__file__).resolve().parents[2],
        )


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
    assert "RUN_INVESTIGATOR" in text and "native subagents" in text
    for name in (
        "curvelens_data_qc", "curvelens_research_planner", "curvelens_investigator",
        "curvelens_synthesizer",
        "curvelens_retrospective",
    ):
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
    assert Path(output["monitor_md"]).exists()
    assert Path(output["monitor_json"]).exists()
    assert Path(output["monitor_events"]).exists()

    inspect = subprocess.run(
        [sys.executable, str(root / "agent" / "analysis_orchestrator.py"),
         "inspect", "--date", "2026-07-20"],
        capture_output=True, text=True, env=env,
    )
    assert inspect.returncode == 0
    inspected = json.loads(inspect.stdout)
    assert inspected["phase"] == "QC_REVIEW_REQUIRED"
    assert inspected["monitor_md"] == output["monitor_md"]


def test_agent_workflow_has_no_direct_model_client_calls():
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "agent" / "run_analysis_workflow.py",
        root / "agent" / "finalize_analysis.py",
        root / "ccvm" / "src" / "ccvm" / "workflow" / "packets.py",
        root / "ccvm" / "src" / "ccvm" / "workflow" / "orchestration.py",
        root / "ccvm" / "src" / "ccvm" / "workflow" / "monitoring.py",
        root / "agent" / "analysis_orchestrator.py",
    ]
    prohibited = ("import openai", "import anthropic", "extract_catalysts", '"claude"')
    for path in files:
        text = path.read_text().lower()
        assert not any(term in text for term in prohibited)


def test_obsolete_script_only_daily_entry_point_is_removed():
    root = Path(__file__).resolve().parents[2]
    assert not (root / "agent" / "run_pipeline.py").exists()

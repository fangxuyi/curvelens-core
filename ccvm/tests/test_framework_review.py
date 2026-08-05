import json
from datetime import date
from pathlib import Path

from ccvm.learning.framework_review import build_framework_review_packet


def _run(
    root: Path, product: str, trade_date: str, *, schema: int,
    failures: list[str], phase: str = "COMPLETE", block_reason: str = "",
) -> None:
    run_dir = root / product / "analysis_workflow" / f"trade_date={trade_date}"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"schema_version": schema}))
    (run_dir / "run.json").write_text(json.dumps({
        "phase": phase, "block_reason": block_reason,
    }))
    events = [
        {
            "event": "validation_rejected", "actor": "synthesis",
            "phase": "SYNTHESIS_REQUIRED", "detail": failure,
        }
        for failure in failures
    ]
    (run_dir / "workflow_events.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in events)
    )


def _memory(root: Path, product: str, *, hit_rate: float = 0.4) -> None:
    path = root / product / "learning" / "memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "as_of": "2026-08-04",
        "entries": [{
            "status": "candidate", "observation": "Product price fact",
            "suggested_adjustment": "Product-only adjustment",
            "scope": {
                "dimension": "price_direction", "horizon_sessions": 1,
                "confidence": "medium",
            },
            "sample_size": 8, "hit_rate": hit_rate, "mean_brier": 0.3,
        }],
        "mobile_entries": [{
            "status": "shadow",
            "scope": {
                "source_view_rank": 2, "expected_materiality": "medium",
                "impact_dimensions": ["price_direction"],
            },
            "sample_size": 8, "recommendation": "neutral",
        }],
        "investigator_entries": [{
            "status": "active",
            "scope": {
                "role": "macro", "horizon_sessions": 1,
                "expected_materiality": "medium",
                "impact_dimensions": ["price_direction"],
            },
            "sample_size": 8, "recommendation": "prefer_dispatch",
        }],
    }))


def test_cross_product_current_schema_failure_is_framework_candidate(tmp_path):
    error_a = "synthesis.top_views[0].key_metrics[0] must be an object"
    error_b = "synthesis.top_views[2].key_metrics[1] must be an object"
    _run(tmp_path, "gold", "2026-08-03", schema=15, failures=[error_a])
    _run(tmp_path, "wti", "2026-08-04", schema=15, failures=[error_b])

    packet = build_framework_review_packet(tmp_path, as_of=date(2026, 8, 5))

    assert packet["summary"]["framework_eligible_count"] == 1
    signal = packet["workflow_signals"][0]
    assert signal["routing"] == "framework_candidate"
    assert signal["products"] == ["gold", "wti"]
    assert signal["normalized_failure"] == (
        "synthesis.top_views[].key_metrics[] must be an object"
    )


def test_old_schema_and_future_runs_do_not_drive_framework_candidates(tmp_path):
    repeated = "synthesis legacy field is invalid"
    _run(tmp_path, "gold", "2026-08-01", schema=14, failures=[repeated] * 4)
    _run(tmp_path, "gold", "2026-08-04", schema=15, failures=[])
    _run(tmp_path, "wti", "2026-08-06", schema=15, failures=[repeated])

    packet = build_framework_review_packet(tmp_path, as_of=date(2026, 8, 5))

    assert packet["workflow_signals"] == []
    gold = next(item for item in packet["run_summaries"] if item["product"] == "gold")
    assert gold["latest_packet_schema"] == 15
    assert gold["current_schema_runs"] == 1


def test_repeated_single_product_shared_failure_requires_review(tmp_path):
    repeated = "mobile candidate must link source-view forecast"
    for day in (1, 2, 3):
        _run(
            tmp_path, "gold", f"2026-08-0{day}", schema=15,
            failures=[repeated],
        )

    packet = build_framework_review_packet(tmp_path, as_of=date(2026, 8, 5))

    signal = packet["workflow_signals"][0]
    assert signal["routing"] == "shared_component_review"
    assert signal["product_count"] == 1
    assert signal["occurrence_count"] == 3


def test_product_memory_is_inventory_only_and_cannot_leak_market_facts(tmp_path):
    _memory(tmp_path, "gold")

    packet = build_framework_review_packet(tmp_path, as_of=date(2026, 8, 5))

    inventory = packet["product_learning_inventory"][0]
    assert inventory["routing"] == "product_local_only"
    assert inventory["framework_eligible"] is False
    assert inventory["families"] == {
        "forecast": {"candidate": 1},
        "mobile": {"shadow": 1},
        "investigator": {"active": 1},
    }
    serialized = json.dumps(packet)
    assert "Product price fact" not in serialized
    assert "Product-only adjustment" not in serialized
    assert all(
        not item["framework_eligible"] for item in packet["learning_pattern_signals"]
    )


def test_consistent_cross_product_memory_pattern_can_enter_framework_review(tmp_path):
    _memory(tmp_path, "gold", hit_rate=0.4)
    _memory(tmp_path, "wti", hit_rate=0.5)

    packet = build_framework_review_packet(tmp_path, as_of=date(2026, 8, 5))

    signal = next(
        item for item in packet["learning_pattern_signals"]
        if item["family"] == "forecast"
    )
    assert signal["routing"] == "framework_candidate"
    assert signal["directions"] == ["underperforming"]
    assert signal["products"] == ["gold", "wti"]
    assert signal["scope"] == {
        "dimension": "price_direction", "horizon_sessions": 1,
        "confidence": "medium",
    }


def test_blocked_current_schema_run_is_a_workflow_signal(tmp_path):
    _run(
        tmp_path, "gold", "2026-08-04", schema=15, failures=[], phase="BLOCKED",
        block_reason="synthesis exceeded correction limit",
    )

    packet = build_framework_review_packet(tmp_path, as_of=date(2026, 8, 5))

    signal = packet["workflow_signals"][0]
    assert signal["component"] == "controller"
    assert signal["occurrences"][0]["event"] == "run_blocked"
    assert signal["routing"] == "insufficient_evidence"

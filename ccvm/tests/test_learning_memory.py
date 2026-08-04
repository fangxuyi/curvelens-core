from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from ccvm.learning.memory import (
    MAX_ACTIVE_ADVISORIES,
    MAX_ACTIVE_MOBILE_ADVISORIES,
    activate_advisory,
    build_memory,
    load_active_snapshot,
    promote_advisory,
)

HASH = "a" * 64


def _evaluation(index, *, dimension="price_direction", hit=True, confidence="high"):
    probability = {"high": 0.8, "medium": 0.65, "low": 0.55}[confidence]
    return {
        "schema_version": 1,
        "forecast_id": f"packet:v1:{dimension}:h1-{index}",
        "dimension": dimension, "horizon_sessions": 1,
        "source_view_rank": index % 3 + 1, "confidence": confidence,
        "expected_label": "up", "realized_label": "up" if hit else "down",
        "status": "scored", "unscored_reason": "", "hit": hit,
        "confidence_probability": probability,
        "brier_loss": (probability - float(hit)) ** 2,
        "rank_weight": [1.0, 0.7, 0.4][index % 3],
        "weighted_hit": [1.0, 0.7, 0.4][index % 3] * float(hit),
        "association": "forecast_associated_with_realized_outcome",
        "analysis_sha256": HASH, "outcome_sha256": HASH,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluator_version": 1,
    }


def _write_evaluations(root, count, *, dimension="price_direction", hit_rate=1.0):
    path = root / "learning" / "evaluations" / "trade_date=2026-07-01"
    path.mkdir(parents=True, exist_ok=True)
    records = [
        _evaluation(i, dimension=dimension, hit=i < int(count * hit_rate))
        for i in range(count)
    ]
    (path / "evaluation.json").write_text(json.dumps({"evaluations": records}))


def _write_shadow_reviews(root, advisory_id, *, hits=True):
    for offset, day in enumerate(range(2, 7), start=100):
        trade_date = f"2026-07-{day:02d}"
        evaluation = root / "learning" / "evaluations" / f"trade_date={trade_date}"
        evaluation.mkdir(parents=True, exist_ok=True)
        (evaluation / "evaluation.json").write_text(json.dumps({
            "evaluations": [_evaluation(offset, hit=hits)],
        }))
        analysis = root / "analysis" / f"trade_date={trade_date}"
        analysis.mkdir(parents=True, exist_ok=True)
        (analysis / "analysis.json").write_text(json.dumps({
            "synthesis": {"memory_feedback": [{
                "advisory_id": advisory_id,
                "disposition": "shadow_would_use",
                "rationale": "Would retain for this shadow review.",
                "evidence_ids": [],
            }]},
        }))


def _mobile_evaluation(
    index, *, material=True, disposition="selected", expected_materiality="high",
):
    score = 1 if material else 0
    selected = disposition == "selected"
    return {
        "schema_version": 1,
        "source_view_rank": 1,
        "disposition": disposition,
        "expected_materiality": expected_materiality,
        "expected_impact_dimensions": ["price_direction"],
        "status": "scored", "unscored_reason": "",
        "realized_materiality": "material" if material else "muted",
        "materiality_score": score,
        "selection_correct": selected == material,
        "missed_material": material and not selected,
        "false_prominence": selected and not material,
        "dimension_results": [{
            "dimension": "price_direction",
            "absolute_change": 0.01 if material else 0.001,
            "realized_materiality": "material" if material else "muted",
            "forecast_id": f"packet:v1:price_direction:h1-{index}",
        }],
        "analysis_sha256": HASH, "outcome_sha256": [HASH],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluator_version": 1,
    }


def _write_mobile_evaluations(root, count, *, material_count=None):
    path = root / "learning" / "evaluations" / "trade_date=2026-07-01"
    path.mkdir(parents=True, exist_ok=True)
    material_count = count if material_count is None else material_count
    records = [_mobile_evaluation(i, material=i < material_count) for i in range(count)]
    (path / "evaluation.json").write_text(json.dumps({
        "evaluations": [],
        "mobile_relevance": {"records": records},
    }))


def _write_mobile_shadow_reviews(root, advisory_id, *, material=True):
    for offset, day in enumerate(range(2, 7), start=200):
        trade_date = f"2026-07-{day:02d}"
        evaluation = root / "learning" / "evaluations" / f"trade_date={trade_date}"
        evaluation.mkdir(parents=True, exist_ok=True)
        (evaluation / "evaluation.json").write_text(json.dumps({
            "evaluations": [],
            "mobile_relevance": {
                "records": [_mobile_evaluation(offset, material=material)],
            },
        }))
        analysis = root / "analysis" / f"trade_date={trade_date}"
        analysis.mkdir(parents=True, exist_ok=True)
        (analysis / "analysis.json").write_text(json.dumps({
            "synthesis": {"mobile_memory_feedback": [{
                "advisory_id": advisory_id,
                "disposition": "shadow_would_use",
                "rationale": "Would use for this shadow review.",
                "evidence_ids": [],
            }]},
        }))


def test_memory_candidate_is_created_at_five_samples(tmp_path):
    _write_evaluations(tmp_path, 5, hit_rate=0.4)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    entry = memory["entries"][0]
    assert entry["status"] == "candidate"
    assert entry["sample_size"] == 5
    assert entry["promotion_eligible"] is False
    assert "Reduce confidence" in entry["suggested_adjustment"]
    assert memory["active_advisories"] == []


def test_promotion_requires_twenty_samples_and_persists(tmp_path):
    _write_evaluations(tmp_path, 19)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    advisory_id = memory["entries"][0]["advisory_id"]
    with pytest.raises(ValueError, match="insufficient"):
        promote_advisory(tmp_path, advisory_id)

    _write_evaluations(tmp_path, 20)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    advisory_id = memory["entries"][0]["advisory_id"]
    promoted = promote_advisory(tmp_path, advisory_id)
    assert promoted["shadow_advisories"][0]["status"] == "shadow"
    assert promoted["active_advisories"] == []
    rebuilt = build_memory(tmp_path, as_of=date(2026, 7, 2))
    assert rebuilt["entries"][0]["status"] == "shadow"
    events = (tmp_path / "learning" / "memory_events.jsonl").read_text()
    assert "candidate_created" in events and "advisory_shadowed" in events

    snapshot = load_active_snapshot(tmp_path, date(2026, 7, 2))
    assert snapshot["advisories"][0]["advisory_id"] == advisory_id
    assert snapshot["advisories"][0]["status"] == "shadow"
    assert len(snapshot["memory_sha256"]) == 64

    with pytest.raises(ValueError, match="no-degradation"):
        activate_advisory(tmp_path, advisory_id)

    _write_shadow_reviews(tmp_path, advisory_id)
    assessed = build_memory(tmp_path, as_of=date(2026, 7, 7))
    assert assessed["entries"][0]["shadow_evaluation"]["no_degradation_passed"] is True
    assert assessed["entries"][0]["shadow_evaluation"]["replay_passed"] is True
    activated = activate_advisory(tmp_path, advisory_id)
    assert activated["active_advisories"][0]["status"] == "active"
    _write_shadow_reviews(tmp_path, advisory_id, hits=False)
    retired = build_memory(tmp_path, as_of=date(2026, 7, 7))
    assert retired["entries"][0]["status"] == "retired"
    assert retired["active_advisories"] == []


def test_snapshot_rejects_lookahead_memory(tmp_path):
    _write_evaluations(tmp_path, 20)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    promote_advisory(tmp_path, memory["entries"][0]["advisory_id"])
    snapshot = load_active_snapshot(tmp_path, date(2026, 7, 1))
    assert snapshot["advisories"] == []
    assert "newer than" in snapshot["unavailable_reason"]


def test_memory_is_product_isolated_by_runtime_root(tmp_path):
    gold = tmp_path / "gold"
    corn = tmp_path / "corn"
    _write_evaluations(gold, 5, dimension="price_direction")
    _write_evaluations(corn, 5, dimension="volatility_direction")
    gold_memory = build_memory(gold, as_of=date(2026, 7, 2))
    corn_memory = build_memory(corn, as_of=date(2026, 7, 2))
    assert gold_memory["entries"][0]["scope"]["dimension"] == "price_direction"
    assert corn_memory["entries"][0]["scope"]["dimension"] == "volatility_direction"
    assert (gold / "learning" / "memory.json").exists()
    assert (corn / "learning" / "memory.json").exists()


def test_malformed_evaluation_is_reported_not_learned(tmp_path):
    path = tmp_path / "learning" / "evaluations" / "trade_date=2026-07-01"
    path.mkdir(parents=True)
    (path / "evaluation.json").write_text('{"evaluations": [{"bad": true}]}')
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    assert memory["entries"] == []
    assert memory["source_errors"][0]["path"].endswith("evaluation.json")


def test_memory_caps_entries_deterministically(tmp_path):
    path = tmp_path / "learning" / "evaluations" / "trade_date=2026-07-01"
    path.mkdir(parents=True)
    records = [
        _evaluation(i * 5 + sample, dimension=f"dimension_{i}")
        for i in range(12) for sample in range(5)
    ]
    (path / "evaluation.json").write_text(json.dumps({"evaluations": records}))
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2), max_entries=10)
    assert len(memory["entries"]) == 10
    assert [item["advisory_id"] for item in memory["entries"]] == sorted(
        item["advisory_id"] for item in memory["entries"]
    )
    assert memory["thresholds"]["max_active_advisories"] == MAX_ACTIVE_ADVISORIES


def test_mobile_candidate_learns_materiality_separately_from_forecast_accuracy(tmp_path):
    _write_mobile_evaluations(tmp_path, 5, material_count=1)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    entry = memory["mobile_entries"][0]

    assert entry["status"] == "candidate"
    assert entry["sample_size"] == 5
    assert entry["recommendation"] == "prefer_omit"
    assert entry["promotion_eligible"] is False
    assert memory["active_mobile_advisories"] == []


def test_mobile_promotion_shadow_activation_and_snapshot_are_guarded(tmp_path):
    _write_mobile_evaluations(tmp_path, 20)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    advisory_id = memory["mobile_entries"][0]["advisory_id"]
    promoted = promote_advisory(tmp_path, advisory_id)

    assert promoted["shadow_mobile_advisories"][0]["status"] == "shadow"
    with pytest.raises(ValueError, match="no-degradation"):
        activate_advisory(tmp_path, advisory_id)

    _write_mobile_shadow_reviews(tmp_path, advisory_id)
    assessed = build_memory(tmp_path, as_of=date(2026, 7, 7))
    shadow = assessed["shadow_mobile_advisories"][0]
    assert shadow["shadow_evaluation"]["review_count"] == 5
    assert shadow["shadow_evaluation"]["no_degradation_passed"] is True
    activated = activate_advisory(tmp_path, advisory_id)
    assert activated["active_mobile_advisories"][0]["status"] == "active"
    assert len(activated["active_mobile_advisories"]) <= MAX_ACTIVE_MOBILE_ADVISORIES

    snapshot = load_active_snapshot(tmp_path, date(2026, 7, 7))
    assert snapshot["mobile_advisories"][0]["advisory_id"] == advisory_id
    assert snapshot["mobile_advisories"][0]["status"] == "active"


def test_mobile_shadow_rejects_any_increase_in_missed_material_rate(tmp_path):
    _write_mobile_evaluations(tmp_path, 20, material_count=0)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    advisory_id = memory["mobile_entries"][0]["advisory_id"]
    promote_advisory(tmp_path, advisory_id)
    _write_mobile_shadow_reviews(tmp_path, advisory_id, material=True)

    assessed = build_memory(tmp_path, as_of=date(2026, 7, 7))
    shadow = assessed["shadow_mobile_advisories"][0]
    assert shadow["recommendation"] == "prefer_omit"
    assert shadow["shadow_evaluation"]["no_degradation_passed"] is False
    with pytest.raises(ValueError, match="no-degradation"):
        activate_advisory(tmp_path, advisory_id)


def test_mobile_memory_is_product_isolated(tmp_path):
    gold = tmp_path / "gold"
    wti = tmp_path / "wti"
    _write_mobile_evaluations(gold, 5, material_count=5)
    _write_mobile_evaluations(wti, 5, material_count=0)

    gold_memory = build_memory(gold, as_of=date(2026, 7, 2))
    wti_memory = build_memory(wti, as_of=date(2026, 7, 2))

    assert gold_memory["mobile_entries"][0]["recommendation"] == "prefer_select"
    assert wti_memory["mobile_entries"][0]["recommendation"] == "prefer_omit"


def test_schema_one_memory_remains_readable_during_mobile_upgrade(tmp_path):
    _write_evaluations(tmp_path, 20)
    memory = build_memory(tmp_path, as_of=date(2026, 7, 2))
    advisory_id = memory["entries"][0]["advisory_id"]
    promoted = promote_advisory(tmp_path, advisory_id)
    promoted["schema_version"] = 1
    promoted.pop("mobile_entries", None)
    promoted.pop("active_mobile_advisories", None)
    promoted.pop("shadow_mobile_advisories", None)
    (tmp_path / "learning" / "memory.json").write_text(json.dumps(promoted))

    snapshot = load_active_snapshot(tmp_path, date(2026, 7, 2))

    assert snapshot["advisories"][0]["advisory_id"] == advisory_id
    assert snapshot["mobile_advisories"] == []
    assert snapshot["schema_version"] == 2

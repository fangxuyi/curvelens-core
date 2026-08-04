from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from ccvm.learning.memory import (
    MAX_ACTIVE_ADVISORIES,
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
    assert promoted["active_advisories"][0]["status"] == "active"
    rebuilt = build_memory(tmp_path, as_of=date(2026, 7, 2))
    assert rebuilt["entries"][0]["status"] == "active"
    events = (tmp_path / "learning" / "memory_events.jsonl").read_text()
    assert "candidate_created" in events and "advisory_promoted" in events

    snapshot = load_active_snapshot(tmp_path, date(2026, 7, 2))
    assert snapshot["advisories"][0]["advisory_id"] == advisory_id
    assert len(snapshot["memory_sha256"]) == 64


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

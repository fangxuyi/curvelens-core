"""Build bounded advisories from accumulated deterministic evaluations."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ccvm.schemas.learning import EvaluationRecord, LearningAdvisory

MEMORY_SCHEMA_VERSION = 1
CANDIDATE_MIN_SAMPLES = 5
PROMOTION_MIN_SAMPLES = 20
MAX_ENTRIES = 50
MAX_ACTIVE_ADVISORIES = 8


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, indent=2, default=str))
    temp.replace(path)


def _memory_paths(data_root: Path) -> tuple[Path, Path]:
    root = data_root / "learning"
    return root / "memory.json", root / "memory_events.jsonl"


def _load_existing(path: Path) -> dict[str, LearningAdvisory]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        return {
            item.advisory_id: item
            for item in (LearningAdvisory.model_validate(raw) for raw in entries)
        }
    except (json.JSONDecodeError, ValueError):
        return {}


def _event(path: Path, event: str, advisory_id: str, **details: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({
            "schema_version": MEMORY_SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event, "advisory_id": advisory_id, **details,
        }) + "\n")


def _advisory_text(dimension: str, horizon: int, confidence: str,
                   hit_rate: float, mean_brier: float) -> tuple[str, str]:
    scope = f"{confidence}-confidence {dimension} forecasts over {horizon} session(s)"
    observation = (
        f"Observed hit rate was {hit_rate:.1%} with mean Brier loss {mean_brier:.3f} "
        f"for {scope}."
    )
    if hit_rate < 0.55 or mean_brier > 0.25:
        adjustment = (
            "Reduce confidence unless current validated evidence is cross-supported; "
            "retain the current forecast when evidence disagrees with this historical aggregate."
        )
    elif hit_rate >= 0.65 and mean_brier <= 0.20:
        adjustment = (
            "This pattern has been comparatively reliable, but require current validated evidence "
            "and preserve conflicting signals before retaining confidence."
        )
    else:
        adjustment = (
            "Keep confidence bounded and give current conflicting evidence priority over this mixed history."
        )
    return observation, adjustment


def build_memory(
    data_root: Path, *, as_of: date | None = None,
    candidate_min_samples: int = CANDIDATE_MIN_SAMPLES,
    promotion_min_samples: int = PROMOTION_MIN_SAMPLES,
    max_entries: int = MAX_ENTRIES,
) -> dict[str, Any]:
    if not 1 <= candidate_min_samples <= promotion_min_samples:
        raise ValueError("memory sample thresholds are invalid")
    if max_entries < 1:
        raise ValueError("max_entries must be positive")
    as_of = as_of or date.today()
    memory_path, events_path = _memory_paths(data_root)
    existing = _load_existing(memory_path)
    groups: dict[tuple[str, int, str], list[tuple[EvaluationRecord, str]]] = defaultdict(list)
    source_hashes: dict[str, str] = {}
    errors = []
    pattern = data_root / "learning" / "evaluations"
    for path in sorted(pattern.glob("trade_date=*/evaluation.json")):
        try:
            trade_date = date.fromisoformat(path.parent.name.removeprefix("trade_date="))
            if trade_date > as_of:
                continue
            payload = json.loads(path.read_text())
            records = [EvaluationRecord.model_validate(item) for item in payload["evaluations"]]
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        source_hashes[str(path)] = _hash(path)
        for record in records:
            if record.status == "scored":
                groups[(record.dimension, record.horizon_sessions, record.confidence)].append(
                    (record, source_hashes[str(path)])
                )

    now = datetime.now(timezone.utc)
    entries: list[LearningAdvisory] = []
    for (dimension, horizon, confidence), pairs in groups.items():
        pairs = pairs[-252:]
        records = [item[0] for item in pairs]
        if len(records) < candidate_min_samples:
            continue
        hits = sum(bool(item.hit) for item in records)
        hit_rate = hits / len(records)
        mean_brier = sum(item.brier_loss or 0 for item in records) / len(records)
        identity = f"{dimension}|{horizon}|{confidence}"
        advisory_id = "learning:" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        previous = existing.get(advisory_id)
        status = previous.status if previous else "candidate"
        observation, adjustment = _advisory_text(
            dimension, horizon, confidence, hit_rate, mean_brier,
        )
        source_evaluation_hashes = sorted({item[1] for item in pairs})
        advisory = LearningAdvisory(
            advisory_id=advisory_id, status=status,
            scope={"dimension": dimension, "horizon_sessions": horizon,
                   "confidence": confidence},
            observation=observation, suggested_adjustment=adjustment,
            sample_size=len(records), hits=hits, hit_rate=round(hit_rate, 6),
            mean_brier=round(mean_brier, 6),
            promotion_eligible=len(records) >= promotion_min_samples,
            source_evaluation_sha256=source_evaluation_hashes,
            created_at=previous.created_at if previous else now, updated_at=now,
        )
        entries.append(advisory)
        if previous is None:
            _event(events_path, "candidate_created", advisory_id, sample_size=len(records))

    entries.sort(key=lambda item: (-item.sample_size, item.advisory_id))
    entries = entries[:max_entries]
    active = [item for item in entries if item.status == "active"]
    if len(active) > MAX_ACTIVE_ADVISORIES:
        raise ValueError("active learning advisories exceed the safety cap")
    result = {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "generated_at": now.isoformat(), "as_of": as_of.isoformat(),
        "thresholds": {
            "candidate_min_samples": candidate_min_samples,
            "promotion_min_samples": promotion_min_samples,
            "max_entries": max_entries,
            "max_active_advisories": MAX_ACTIVE_ADVISORIES,
        },
        "source_evaluations": source_hashes, "source_errors": errors,
        "entries": [item.model_dump(mode="json") for item in entries],
        "active_advisories": [item.model_dump(mode="json") for item in active],
    }
    _write_json(memory_path, result)
    return result


def promote_advisory(data_root: Path, advisory_id: str) -> dict[str, Any]:
    memory_path, events_path = _memory_paths(data_root)
    if not memory_path.exists():
        raise ValueError("learning memory does not exist; run learn first")
    payload = json.loads(memory_path.read_text())
    entries = [LearningAdvisory.model_validate(item) for item in payload.get("entries", [])]
    target = next((item for item in entries if item.advisory_id == advisory_id), None)
    if target is None:
        raise ValueError(f"unknown learning advisory: {advisory_id}")
    if not target.promotion_eligible:
        raise ValueError("learning advisory has insufficient scored samples for promotion")
    active_count = sum(item.status == "active" for item in entries)
    if target.status != "active" and active_count >= MAX_ACTIVE_ADVISORIES:
        raise ValueError("active learning advisory cap reached")
    now = datetime.now(timezone.utc)
    updated = [
        item.model_copy(update={"status": "active", "updated_at": now})
        if item.advisory_id == advisory_id else item
        for item in entries
    ]
    payload["generated_at"] = now.isoformat()
    payload["entries"] = [item.model_dump(mode="json") for item in updated]
    payload["active_advisories"] = [
        item.model_dump(mode="json") for item in updated if item.status == "active"
    ]
    _write_json(memory_path, payload)
    _event(events_path, "advisory_promoted", advisory_id, sample_size=target.sample_size)
    return payload


__all__ = [
    "CANDIDATE_MIN_SAMPLES", "MAX_ACTIVE_ADVISORIES", "MAX_ENTRIES",
    "PROMOTION_MIN_SAMPLES", "build_memory", "promote_advisory",
]

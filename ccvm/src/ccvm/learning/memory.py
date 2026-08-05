"""Build bounded advisories from accumulated deterministic evaluations."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ccvm.schemas.learning import (
    EvaluationRecord,
    InvestigatorEvaluationRecord,
    InvestigatorLearningAdvisory,
    LearningAdvisory,
    MobileLearningAdvisory,
)
from ccvm.schemas.reporting import MobileRelevanceEvaluation

MEMORY_SCHEMA_VERSION = 3
CANDIDATE_MIN_SAMPLES = 5
PROMOTION_MIN_SAMPLES = 20
MAX_ENTRIES = 50
MAX_ACTIVE_ADVISORIES = 8
MAX_SHADOW_ADVISORIES = 8
MIN_SHADOW_REVIEWS = 5
MAX_MOBILE_ENTRIES = 24
MAX_ACTIVE_MOBILE_ADVISORIES = 4
MAX_SHADOW_MOBILE_ADVISORIES = 4
MAX_INVESTIGATOR_ENTRIES = 24
MAX_ACTIVE_INVESTIGATOR_ADVISORIES = 4
MAX_SHADOW_INVESTIGATOR_ADVISORIES = 4


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


def _load_existing_mobile(path: Path) -> dict[str, MobileLearningAdvisory]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
        entries = payload.get("mobile_entries", []) if isinstance(payload, dict) else []
        return {
            item.advisory_id: item
            for item in (MobileLearningAdvisory.model_validate(raw) for raw in entries)
        }
    except (json.JSONDecodeError, ValueError):
        return {}


def _load_existing_investigator(path: Path) -> dict[str, InvestigatorLearningAdvisory]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
        entries = payload.get("investigator_entries", []) if isinstance(payload, dict) else []
        return {
            item.advisory_id: item
            for item in (InvestigatorLearningAdvisory.model_validate(raw) for raw in entries)
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


def _mobile_advisory_text(
    rank: int, materiality: str, dimensions: tuple[str, ...], material_rate: float,
) -> tuple[str, str, str]:
    scope = (
        f"rank-{rank} {materiality}-expected-materiality views linked to "
        f"{', '.join(dimensions)}"
    )
    observation = f"Realized next-session materiality was {material_rate:.1%} for {scope}."
    if material_rate >= 0.65:
        return (
            observation, "prefer_select",
            "Prefer mobile selection when current validated evidence matches this scope; "
            "reject the advisory when a stronger current conflict or limitation changes the conclusion.",
        )
    if material_rate <= 0.35:
        return (
            observation, "prefer_omit",
            "Keep this scope in the full report unless current validated evidence establishes "
            "independent next-session materiality.",
        )
    return (
        observation, "neutral",
        "Historical materiality is mixed; preserve the current evidence-based mobile decision.",
    )


def _investigator_advisory_text(
    role: str, horizon: int, materiality: str, dimensions: tuple[str, ...],
    material_rate: float,
) -> tuple[str, str, str]:
    scope = (
        f"{role} findings over {horizon} session(s), expected {materiality}, linked to "
        f"{', '.join(dimensions)}"
    )
    observation = f"Realized materiality was {material_rate:.1%} for {scope}."
    if material_rate >= 0.65:
        return (
            observation, "prefer_dispatch",
            "Prefer a targeted dispatch when current canonical evidence matches this scope; "
            "reject the advisory when today's evidence does not present a decision-relevant question.",
        )
    if material_rate <= 0.25:
        return (
            observation, "prefer_skip",
            "Prefer omission for this scope unless current canonical evidence identifies a new anomaly "
            "that could materially change the final analysis.",
        )
    return (
        observation, "neutral",
        "Historical materiality is mixed; preserve the current evidence-based dispatch decision.",
    )


def _investigator_matching(
    record: InvestigatorEvaluationRecord, advisory: InvestigatorLearningAdvisory,
) -> bool:
    return (
        record.status == "scored"
        and record.role == advisory.scope.role
        and record.horizon_sessions == advisory.scope.horizon_sessions
        and record.expected_materiality == advisory.scope.expected_materiality
        and record.expected_impact_dimensions == advisory.scope.impact_dimensions
    )


def _investigator_metrics(records: list[InvestigatorEvaluationRecord]) -> dict[str, float]:
    if not records:
        return {
            "material_rate": 0.0, "materiality_hit_rate": 0.0,
            "lead_use_rate": 0.0, "rejected_material_rate": 0.0,
        }
    rejected = [item for item in records if not item.lead_used_finding]
    return {
        "material_rate": sum(bool(item.material) for item in records) / len(records),
        "materiality_hit_rate": sum(bool(item.materiality_hit) for item in records) / len(records),
        "lead_use_rate": sum(bool(item.lead_used_finding) for item in records) / len(records),
        "rejected_material_rate": (
            sum(bool(item.material) for item in rejected) / len(rejected) if rejected else 0.0
        ),
    }


def assess_investigator_shadow(
    data_root: Path, advisory: InvestigatorLearningAdvisory,
) -> dict[str, Any]:
    """Evaluate shadow planning advice without allowing it to change dispatch."""
    baseline: list[InvestigatorEvaluationRecord] = []
    shadow: list[InvestigatorEvaluationRecord] = []
    review_count = 0
    would_use_count = 0
    evaluations_root = data_root / "learning" / "evaluations"
    for path in sorted(evaluations_root.glob("trade_date=*/evaluation.json")):
        try:
            payload = json.loads(path.read_text())
            records = [
                InvestigatorEvaluationRecord.model_validate(item)
                for item in (payload.get("investigator_relevance") or {}).get("records", [])
            ]
        except (ValueError, json.JSONDecodeError):
            continue
        scoped = [item for item in records if _investigator_matching(item, advisory)]
        baseline.extend(scoped)
        if not scoped:
            continue
        trade_date = path.parent.name.removeprefix("trade_date=")
        analysis_path = data_root / "analysis" / f"trade_date={trade_date}" / "analysis.json"
        if not analysis_path.exists():
            continue
        try:
            analysis = json.loads(analysis_path.read_text())
        except json.JSONDecodeError:
            continue
        feedback = (analysis.get("research_plan") or {}).get(
            "investigator_memory_feedback", []
        )
        item = next((
            value for value in feedback
            if isinstance(value, dict) and value.get("advisory_id") == advisory.advisory_id
            and value.get("disposition") in {"shadow_would_use", "shadow_rejected"}
        ), None)
        if item is None:
            continue
        review_count += 1
        if item["disposition"] == "shadow_would_use":
            would_use_count += 1
            shadow.extend(scoped)

    baseline_metrics = _investigator_metrics(baseline)
    shadow_metrics = _investigator_metrics(shadow)

    def recommendation_passed(metrics: dict[str, float]) -> bool:
        if advisory.recommendation == "prefer_dispatch":
            return metrics["material_rate"] >= 0.60
        if advisory.recommendation == "prefer_skip":
            return metrics["material_rate"] <= 0.30
        return False

    replay_passed = bool(
        len(baseline) >= PROMOTION_MIN_SAMPLES and recommendation_passed(baseline_metrics)
    )
    no_degradation = bool(
        recommendation_passed(shadow_metrics)
        and (
            shadow_metrics["material_rate"] >= baseline_metrics["material_rate"] - 0.05
            if advisory.recommendation == "prefer_dispatch"
            else shadow_metrics["material_rate"] <= baseline_metrics["material_rate"] + 0.05
        )
    )
    shadow_passed = bool(
        review_count >= MIN_SHADOW_REVIEWS
        and would_use_count >= MIN_SHADOW_REVIEWS
        and len(shadow) >= MIN_SHADOW_REVIEWS
        and no_degradation
    )
    return {
        "review_count": review_count,
        "would_use_count": would_use_count,
        "scored": len(shadow),
        "baseline_scored": len(baseline),
        "baseline": {key: round(value, 6) for key, value in baseline_metrics.items()},
        "shadow": {key: round(value, 6) for key, value in shadow_metrics.items()},
        "replay_passed": replay_passed,
        "shadow_passed": shadow_passed,
        "no_degradation_passed": replay_passed and shadow_passed,
        "rule": (
            "prefer_dispatch requires >=60% materiality; prefer_skip requires <=30%; "
            "shadow materiality may not degrade by more than 5pp"
        ),
    }


def _mobile_matching(
    record: MobileRelevanceEvaluation, advisory: MobileLearningAdvisory,
) -> bool:
    return (
        record.status == "scored"
        and record.source_view_rank == advisory.scope.source_view_rank
        and record.expected_materiality == advisory.scope.expected_materiality
        and sorted(record.expected_impact_dimensions) == advisory.scope.impact_dimensions
    )


def _mobile_actual_metrics(records: list[MobileRelevanceEvaluation]) -> dict[str, float]:
    if not records:
        return {"accuracy": 0.0, "missed": 0.0, "false_prominence": 0.0}
    return {
        "accuracy": sum(bool(item.selection_correct) for item in records) / len(records),
        "missed": sum(bool(item.missed_material) for item in records) / len(records),
        "false_prominence": sum(bool(item.false_prominence) for item in records) / len(records),
    }


def _mobile_recommendation_metrics(
    records: list[MobileRelevanceEvaluation], recommendation: str,
) -> dict[str, float]:
    if not records:
        return {"accuracy": 0.0, "missed": 0.0, "false_prominence": 0.0}
    prefer_select = recommendation == "prefer_select"
    material = [bool(item.materiality_score and item.materiality_score >= 1) for item in records]
    return {
        "accuracy": sum(value == prefer_select for value in material) / len(records),
        "missed": sum(value and not prefer_select for value in material) / len(records),
        "false_prominence": sum(not value and prefer_select for value in material) / len(records),
    }


def _matching(record: EvaluationRecord, advisory: LearningAdvisory) -> bool:
    return (
        record.status == "scored"
        and record.dimension == advisory.scope.dimension
        and record.horizon_sessions == advisory.scope.horizon_sessions
        and record.confidence == advisory.scope.confidence
    )


def assess_shadow(data_root: Path, advisory: LearningAdvisory) -> dict[str, Any]:
    """Compare shadow-reviewed dates with the same scope's historical baseline."""
    baseline: list[EvaluationRecord] = []
    shadow: list[EvaluationRecord] = []
    review_count = 0
    evaluations_root = data_root / "learning" / "evaluations"
    for path in sorted(evaluations_root.glob("trade_date=*/evaluation.json")):
        try:
            payload = json.loads(path.read_text())
            records = [
                EvaluationRecord.model_validate(item)
                for item in payload.get("evaluations", [])
            ]
        except (ValueError, json.JSONDecodeError):
            continue
        scoped = [item for item in records if _matching(item, advisory)]
        baseline.extend(scoped)
        trade_date = path.parent.name.removeprefix("trade_date=")
        analysis_path = data_root / "analysis" / f"trade_date={trade_date}" / "analysis.json"
        if not analysis_path.exists():
            continue
        try:
            analysis = json.loads(analysis_path.read_text())
        except json.JSONDecodeError:
            continue
        feedback = (analysis.get("synthesis") or {}).get("memory_feedback", [])
        item = next((
            value for value in feedback
            if isinstance(value, dict) and value.get("advisory_id") == advisory.advisory_id
            and value.get("disposition") in {"shadow_would_use", "shadow_rejected"}
        ), None)
        if item is not None:
            review_count += 1
            shadow.extend(scoped)

    def metrics(records: list[EvaluationRecord]) -> tuple[float | None, float | None]:
        if not records:
            return None, None
        return (
            sum(bool(item.hit) for item in records) / len(records),
            sum(item.brier_loss or 0 for item in records) / len(records),
        )

    shadow_hit, shadow_brier = metrics(shadow)
    baseline_hit, baseline_brier = metrics(baseline)
    replay_passed = bool(
        len(baseline) >= PROMOTION_MIN_SAMPLES
        and baseline_hit is not None and baseline_brier is not None
        and baseline_hit >= advisory.hit_rate - 0.05
        and baseline_brier <= advisory.mean_brier + 0.05
    )
    shadow_passed = bool(
        review_count >= MIN_SHADOW_REVIEWS
        and len(shadow) >= MIN_SHADOW_REVIEWS
        and shadow_hit is not None and baseline_hit is not None
        and shadow_brier is not None and baseline_brier is not None
        and shadow_hit >= baseline_hit - 0.05
        and shadow_brier <= baseline_brier + 0.05
    )
    return {
        "review_count": review_count, "scored": len(shadow),
        "hit_rate": round(shadow_hit, 6) if shadow_hit is not None else None,
        "mean_brier": round(shadow_brier, 6) if shadow_brier is not None else None,
        "baseline_scored": len(baseline),
        "baseline_hit_rate": round(baseline_hit, 6) if baseline_hit is not None else None,
        "baseline_mean_brier": round(baseline_brier, 6) if baseline_brier is not None else None,
        "replay_passed": replay_passed,
        "shadow_passed": shadow_passed,
        "no_degradation_passed": replay_passed and shadow_passed,
        "rule": "shadow hit rate >= baseline - 5pp and Brier <= baseline + 0.05",
    }


def assess_mobile_shadow(
    data_root: Path, advisory: MobileLearningAdvisory,
) -> dict[str, Any]:
    """Evaluate mobile advice without allowing shadow feedback to affect reports."""
    baseline: list[MobileRelevanceEvaluation] = []
    shadow: list[MobileRelevanceEvaluation] = []
    review_count = 0
    would_use_count = 0
    evaluations_root = data_root / "learning" / "evaluations"
    for path in sorted(evaluations_root.glob("trade_date=*/evaluation.json")):
        try:
            payload = json.loads(path.read_text())
            records = [
                MobileRelevanceEvaluation.model_validate(item)
                for item in (payload.get("mobile_relevance") or {}).get("records", [])
            ]
        except (ValueError, json.JSONDecodeError):
            continue
        scoped = [item for item in records if _mobile_matching(item, advisory)]
        baseline.extend(scoped)
        if not scoped:
            continue
        trade_date = path.parent.name.removeprefix("trade_date=")
        analysis_path = data_root / "analysis" / f"trade_date={trade_date}" / "analysis.json"
        if not analysis_path.exists():
            continue
        try:
            analysis = json.loads(analysis_path.read_text())
        except json.JSONDecodeError:
            continue
        feedback = (analysis.get("synthesis") or {}).get("mobile_memory_feedback", [])
        item = next((
            value for value in feedback
            if isinstance(value, dict) and value.get("advisory_id") == advisory.advisory_id
            and value.get("disposition") in {"shadow_would_use", "shadow_rejected"}
        ), None)
        if item is None:
            continue
        review_count += 1
        if item["disposition"] == "shadow_would_use":
            would_use_count += 1
            shadow.extend(scoped)

    baseline_actual = _mobile_actual_metrics(baseline)
    baseline_recommended = _mobile_recommendation_metrics(
        baseline, advisory.recommendation,
    )
    shadow_actual = _mobile_actual_metrics(shadow)
    shadow_recommended = _mobile_recommendation_metrics(
        shadow, advisory.recommendation,
    )

    def no_degradation(actual: dict[str, float], recommended: dict[str, float]) -> bool:
        return bool(
            recommended["accuracy"] >= actual["accuracy"] - 0.05
            and recommended["missed"] <= actual["missed"]
            and recommended["false_prominence"] <= actual["false_prominence"] + 0.05
        )

    replay_passed = bool(
        advisory.recommendation != "neutral"
        and len(baseline) >= PROMOTION_MIN_SAMPLES
        and no_degradation(baseline_actual, baseline_recommended)
    )
    shadow_passed = bool(
        review_count >= MIN_SHADOW_REVIEWS
        and would_use_count >= MIN_SHADOW_REVIEWS
        and len(shadow) >= MIN_SHADOW_REVIEWS
        and no_degradation(shadow_actual, shadow_recommended)
    )
    return {
        "review_count": review_count,
        "would_use_count": would_use_count,
        "scored": len(shadow),
        "baseline_scored": len(baseline),
        "baseline_actual": {key: round(value, 6) for key, value in baseline_actual.items()},
        "baseline_recommended": {
            key: round(value, 6) for key, value in baseline_recommended.items()
        },
        "shadow_actual": {key: round(value, 6) for key, value in shadow_actual.items()},
        "shadow_recommended": {
            key: round(value, 6) for key, value in shadow_recommended.items()
        },
        "replay_passed": replay_passed,
        "shadow_passed": shadow_passed,
        "no_degradation_passed": replay_passed and shadow_passed,
        "rule": (
            "recommended accuracy >= actual - 5pp, missed-material rate cannot increase, "
            "and false-prominence rate <= actual + 5pp"
        ),
    }


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
    existing_mobile = _load_existing_mobile(memory_path)
    existing_investigator = _load_existing_investigator(memory_path)
    groups: dict[tuple[str, int, str], list[tuple[EvaluationRecord, str]]] = defaultdict(list)
    mobile_groups: dict[
        tuple[int, str, tuple[str, ...]],
        list[tuple[MobileRelevanceEvaluation, str]],
    ] = defaultdict(list)
    investigator_groups: dict[
        tuple[str, int, str, tuple[str, ...]],
        list[tuple[InvestigatorEvaluationRecord, str]],
    ] = defaultdict(list)
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
        try:
            mobile_records = [
                MobileRelevanceEvaluation.model_validate(item)
                for item in (payload.get("mobile_relevance") or {}).get("records", [])
            ]
        except ValueError as exc:
            errors.append({"path": str(path), "kind": "mobile_relevance", "error": str(exc)})
            mobile_records = []
        for record in mobile_records:
            if record.status != "scored":
                continue
            key = (
                record.source_view_rank, record.expected_materiality,
                tuple(sorted(record.expected_impact_dimensions)),
            )
            mobile_groups[key].append((record, source_hashes[str(path)]))
        try:
            investigator_records = [
                InvestigatorEvaluationRecord.model_validate(item)
                for item in (payload.get("investigator_relevance") or {}).get("records", [])
            ]
        except ValueError as exc:
            errors.append({
                "path": str(path), "kind": "investigator_relevance", "error": str(exc),
            })
            investigator_records = []
        for record in investigator_records:
            if record.status != "scored":
                continue
            key = (
                record.role, record.horizon_sessions, record.expected_materiality,
                tuple(record.expected_impact_dimensions),
            )
            investigator_groups[key].append((record, source_hashes[str(path)]))

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
            shadow_evaluation=previous.shadow_evaluation if previous else {},
        )
        if advisory.status in {"shadow", "active"}:
            assessment = assess_shadow(data_root, advisory)
            advisory = advisory.model_copy(update={
                "shadow_evaluation": assessment,
            })
            if advisory.status == "active" and not assessment["no_degradation_passed"]:
                advisory = advisory.model_copy(update={"status": "retired"})
                _event(events_path, "advisory_retired", advisory_id,
                       reason="no-degradation safeguard failed")
        entries.append(advisory)
        if previous is None:
            _event(events_path, "candidate_created", advisory_id, sample_size=len(records))

    entries.sort(key=lambda item: (-item.sample_size, item.advisory_id))
    entries = entries[:max_entries]
    active = [item for item in entries if item.status == "active"]
    shadow = [item for item in entries if item.status == "shadow"]
    if len(active) > MAX_ACTIVE_ADVISORIES:
        raise ValueError("active learning advisories exceed the safety cap")
    if len(shadow) > MAX_SHADOW_ADVISORIES:
        raise ValueError("shadow learning advisories exceed the safety cap")

    mobile_entries: list[MobileLearningAdvisory] = []
    for (rank, materiality, dimensions), pairs in mobile_groups.items():
        pairs = pairs[-252:]
        records = [item[0] for item in pairs]
        if len(records) < candidate_min_samples:
            continue
        material_count = sum(bool(item.materiality_score) for item in records)
        material_rate = material_count / len(records)
        actual = _mobile_actual_metrics(records)
        observation, recommendation, adjustment = _mobile_advisory_text(
            rank, materiality, dimensions, material_rate,
        )
        identity = f"{rank}|{materiality}|{'|'.join(dimensions)}"
        advisory_id = "mobile-learning:" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        previous = existing_mobile.get(advisory_id)
        advisory = MobileLearningAdvisory(
            advisory_id=advisory_id,
            status=previous.status if previous else "candidate",
            scope={
                "source_view_rank": rank,
                "expected_materiality": materiality,
                "impact_dimensions": list(dimensions),
            },
            recommendation=recommendation,
            observation=observation,
            suggested_adjustment=adjustment,
            sample_size=len(records),
            material_count=material_count,
            material_rate=round(material_rate, 6),
            selection_accuracy=round(actual["accuracy"], 6),
            missed_material_rate=round(actual["missed"], 6),
            false_prominence_rate=round(actual["false_prominence"], 6),
            promotion_eligible=(
                len(records) >= promotion_min_samples and recommendation != "neutral"
            ),
            source_evaluation_sha256=sorted({item[1] for item in pairs}),
            created_at=previous.created_at if previous else now,
            updated_at=now,
            shadow_evaluation=previous.shadow_evaluation if previous else {},
        )
        if advisory.status in {"shadow", "active"}:
            assessment = assess_mobile_shadow(data_root, advisory)
            advisory = advisory.model_copy(update={"shadow_evaluation": assessment})
            if advisory.status == "active" and not assessment["no_degradation_passed"]:
                advisory = advisory.model_copy(update={"status": "retired"})
                _event(
                    events_path, "mobile_advisory_retired", advisory_id,
                    reason="mobile no-degradation safeguard failed",
                )
        mobile_entries.append(advisory)
        if previous is None:
            _event(
                events_path, "mobile_candidate_created", advisory_id,
                sample_size=len(records), recommendation=recommendation,
            )

    mobile_entries.sort(key=lambda item: (-item.sample_size, item.advisory_id))
    mobile_entries = mobile_entries[:MAX_MOBILE_ENTRIES]
    active_mobile = [item for item in mobile_entries if item.status == "active"]
    shadow_mobile = [item for item in mobile_entries if item.status == "shadow"]
    if len(active_mobile) > MAX_ACTIVE_MOBILE_ADVISORIES:
        raise ValueError("active mobile learning advisories exceed the safety cap")
    if len(shadow_mobile) > MAX_SHADOW_MOBILE_ADVISORIES:
        raise ValueError("shadow mobile learning advisories exceed the safety cap")

    investigator_entries: list[InvestigatorLearningAdvisory] = []
    for (role, horizon, materiality, dimensions), pairs in investigator_groups.items():
        pairs = pairs[-252:]
        records = [item[0] for item in pairs]
        if len(records) < candidate_min_samples:
            continue
        metrics = _investigator_metrics(records)
        observation, recommendation, adjustment = _investigator_advisory_text(
            role, horizon, materiality, dimensions, metrics["material_rate"],
        )
        identity = f"{role}|{horizon}|{materiality}|{'|'.join(dimensions)}"
        advisory_id = (
            "investigator-learning:" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        )
        previous = existing_investigator.get(advisory_id)
        advisory = InvestigatorLearningAdvisory(
            advisory_id=advisory_id,
            status=previous.status if previous else "candidate",
            scope={
                "role": role, "horizon_sessions": horizon,
                "expected_materiality": materiality,
                "impact_dimensions": list(dimensions),
            },
            recommendation=recommendation,
            observation=observation,
            suggested_adjustment=adjustment,
            sample_size=len(records),
            material_count=sum(bool(item.material) for item in records),
            material_rate=round(metrics["material_rate"], 6),
            materiality_hit_rate=round(metrics["materiality_hit_rate"], 6),
            lead_use_rate=round(metrics["lead_use_rate"], 6),
            rejected_material_rate=round(metrics["rejected_material_rate"], 6),
            promotion_eligible=(
                len(records) >= promotion_min_samples and recommendation != "neutral"
            ),
            source_evaluation_sha256=sorted({item[1] for item in pairs}),
            created_at=previous.created_at if previous else now,
            updated_at=now,
            shadow_evaluation=previous.shadow_evaluation if previous else {},
        )
        if advisory.status in {"shadow", "active"}:
            assessment = assess_investigator_shadow(data_root, advisory)
            advisory = advisory.model_copy(update={"shadow_evaluation": assessment})
            if advisory.status == "active" and not assessment["no_degradation_passed"]:
                advisory = advisory.model_copy(update={"status": "retired"})
                _event(
                    events_path, "investigator_advisory_retired", advisory_id,
                    reason="investigator no-degradation safeguard failed",
                )
        investigator_entries.append(advisory)
        if previous is None:
            _event(
                events_path, "investigator_candidate_created", advisory_id,
                sample_size=len(records), recommendation=recommendation,
            )
    investigator_entries.sort(key=lambda item: (-item.sample_size, item.advisory_id))
    investigator_entries = investigator_entries[:MAX_INVESTIGATOR_ENTRIES]
    active_investigator = [
        item for item in investigator_entries if item.status == "active"
    ]
    shadow_investigator = [
        item for item in investigator_entries if item.status == "shadow"
    ]
    if len(active_investigator) > MAX_ACTIVE_INVESTIGATOR_ADVISORIES:
        raise ValueError("active investigator learning advisories exceed the safety cap")
    if len(shadow_investigator) > MAX_SHADOW_INVESTIGATOR_ADVISORIES:
        raise ValueError("shadow investigator learning advisories exceed the safety cap")
    result = {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "generated_at": now.isoformat(), "as_of": as_of.isoformat(),
        "thresholds": {
            "candidate_min_samples": candidate_min_samples,
            "promotion_min_samples": promotion_min_samples,
            "max_entries": max_entries,
            "max_active_advisories": MAX_ACTIVE_ADVISORIES,
            "max_shadow_advisories": MAX_SHADOW_ADVISORIES,
            "min_shadow_reviews": MIN_SHADOW_REVIEWS,
            "max_mobile_entries": MAX_MOBILE_ENTRIES,
            "max_active_mobile_advisories": MAX_ACTIVE_MOBILE_ADVISORIES,
            "max_shadow_mobile_advisories": MAX_SHADOW_MOBILE_ADVISORIES,
            "max_investigator_entries": MAX_INVESTIGATOR_ENTRIES,
            "max_active_investigator_advisories": MAX_ACTIVE_INVESTIGATOR_ADVISORIES,
            "max_shadow_investigator_advisories": MAX_SHADOW_INVESTIGATOR_ADVISORIES,
        },
        "source_evaluations": source_hashes, "source_errors": errors,
        "entries": [item.model_dump(mode="json") for item in entries],
        "active_advisories": [item.model_dump(mode="json") for item in active],
        "shadow_advisories": [item.model_dump(mode="json") for item in shadow],
        "mobile_entries": [item.model_dump(mode="json") for item in mobile_entries],
        "active_mobile_advisories": [
            item.model_dump(mode="json") for item in active_mobile
        ],
        "shadow_mobile_advisories": [
            item.model_dump(mode="json") for item in shadow_mobile
        ],
        "investigator_entries": [
            item.model_dump(mode="json") for item in investigator_entries
        ],
        "active_investigator_advisories": [
            item.model_dump(mode="json") for item in active_investigator
        ],
        "shadow_investigator_advisories": [
            item.model_dump(mode="json") for item in shadow_investigator
        ],
    }
    _write_json(memory_path, result)
    return result


def _write_mobile_lists(
    payload: dict[str, Any], entries: list[MobileLearningAdvisory], now: datetime,
) -> None:
    payload["generated_at"] = now.isoformat()
    payload["mobile_entries"] = [item.model_dump(mode="json") for item in entries]
    payload["active_mobile_advisories"] = [
        item.model_dump(mode="json") for item in entries if item.status == "active"
    ]
    payload["shadow_mobile_advisories"] = [
        item.model_dump(mode="json") for item in entries if item.status == "shadow"
    ]


def _promote_mobile_advisory(data_root: Path, advisory_id: str) -> dict[str, Any]:
    memory_path, events_path = _memory_paths(data_root)
    if not memory_path.exists():
        raise ValueError("learning memory does not exist; run learn first")
    payload = json.loads(memory_path.read_text())
    entries = [
        MobileLearningAdvisory.model_validate(item)
        for item in payload.get("mobile_entries", [])
    ]
    target = next((item for item in entries if item.advisory_id == advisory_id), None)
    if target is None:
        raise ValueError(f"unknown learning advisory: {advisory_id}")
    if not target.promotion_eligible:
        raise ValueError("learning advisory has insufficient scored samples for promotion")
    if target.status != "candidate":
        raise ValueError("only candidate advisories can enter shadow status")
    if sum(item.status == "shadow" for item in entries) >= MAX_SHADOW_MOBILE_ADVISORIES:
        raise ValueError("shadow mobile learning advisory cap reached")
    now = datetime.now(timezone.utc)
    updated = [
        item.model_copy(update={"status": "shadow", "updated_at": now})
        if item.advisory_id == advisory_id else item
        for item in entries
    ]
    _write_mobile_lists(payload, updated, now)
    _write_json(memory_path, payload)
    _event(
        events_path, "mobile_advisory_shadowed", advisory_id,
        sample_size=target.sample_size,
    )
    return payload


def _activate_mobile_advisory(data_root: Path, advisory_id: str) -> dict[str, Any]:
    memory_path, events_path = _memory_paths(data_root)
    if not memory_path.exists():
        raise ValueError("learning memory does not exist; run learn first")
    payload = json.loads(memory_path.read_text())
    entries = [
        MobileLearningAdvisory.model_validate(item)
        for item in payload.get("mobile_entries", [])
    ]
    target = next((item for item in entries if item.advisory_id == advisory_id), None)
    if target is None or target.status != "shadow":
        raise ValueError("learning advisory must be in shadow status before activation")
    assessment = assess_mobile_shadow(data_root, target)
    if not assessment["no_degradation_passed"]:
        raise ValueError("learning advisory has not passed shadow no-degradation safeguards")
    if sum(item.status == "active" for item in entries) >= MAX_ACTIVE_MOBILE_ADVISORIES:
        raise ValueError("active mobile learning advisory cap reached")
    now = datetime.now(timezone.utc)
    updated = [
        item.model_copy(update={
            "status": "active", "updated_at": now,
            "shadow_evaluation": assessment,
        }) if item.advisory_id == advisory_id else item
        for item in entries
    ]
    _write_mobile_lists(payload, updated, now)
    _write_json(memory_path, payload)
    _event(
        events_path, "mobile_advisory_activated", advisory_id,
        shadow_evaluation=assessment,
    )
    return payload


def _write_investigator_lists(
    payload: dict[str, Any], entries: list[InvestigatorLearningAdvisory], now: datetime,
) -> None:
    payload["generated_at"] = now.isoformat()
    payload["investigator_entries"] = [item.model_dump(mode="json") for item in entries]
    payload["active_investigator_advisories"] = [
        item.model_dump(mode="json") for item in entries if item.status == "active"
    ]
    payload["shadow_investigator_advisories"] = [
        item.model_dump(mode="json") for item in entries if item.status == "shadow"
    ]


def _promote_investigator_advisory(data_root: Path, advisory_id: str) -> dict[str, Any]:
    memory_path, events_path = _memory_paths(data_root)
    if not memory_path.exists():
        raise ValueError("learning memory does not exist; run learn first")
    payload = json.loads(memory_path.read_text())
    entries = [
        InvestigatorLearningAdvisory.model_validate(item)
        for item in payload.get("investigator_entries", [])
    ]
    target = next((item for item in entries if item.advisory_id == advisory_id), None)
    if target is None:
        raise ValueError(f"unknown learning advisory: {advisory_id}")
    if not target.promotion_eligible:
        raise ValueError("learning advisory has insufficient scored samples for promotion")
    if target.status != "candidate":
        raise ValueError("only candidate advisories can enter shadow status")
    if sum(item.status == "shadow" for item in entries) >= MAX_SHADOW_INVESTIGATOR_ADVISORIES:
        raise ValueError("shadow investigator learning advisory cap reached")
    now = datetime.now(timezone.utc)
    updated = [
        item.model_copy(update={"status": "shadow", "updated_at": now})
        if item.advisory_id == advisory_id else item
        for item in entries
    ]
    _write_investigator_lists(payload, updated, now)
    _write_json(memory_path, payload)
    _event(
        events_path, "investigator_advisory_shadowed", advisory_id,
        sample_size=target.sample_size,
    )
    return payload


def _activate_investigator_advisory(data_root: Path, advisory_id: str) -> dict[str, Any]:
    memory_path, events_path = _memory_paths(data_root)
    if not memory_path.exists():
        raise ValueError("learning memory does not exist; run learn first")
    payload = json.loads(memory_path.read_text())
    entries = [
        InvestigatorLearningAdvisory.model_validate(item)
        for item in payload.get("investigator_entries", [])
    ]
    target = next((item for item in entries if item.advisory_id == advisory_id), None)
    if target is None or target.status != "shadow":
        raise ValueError("learning advisory must be in shadow status before activation")
    assessment = assess_investigator_shadow(data_root, target)
    if not assessment["no_degradation_passed"]:
        raise ValueError("learning advisory has not passed shadow no-degradation safeguards")
    if sum(item.status == "active" for item in entries) >= MAX_ACTIVE_INVESTIGATOR_ADVISORIES:
        raise ValueError("active investigator learning advisory cap reached")
    now = datetime.now(timezone.utc)
    updated = [
        item.model_copy(update={
            "status": "active", "updated_at": now, "shadow_evaluation": assessment,
        }) if item.advisory_id == advisory_id else item
        for item in entries
    ]
    _write_investigator_lists(payload, updated, now)
    _write_json(memory_path, payload)
    _event(
        events_path, "investigator_advisory_activated", advisory_id,
        shadow_evaluation=assessment,
    )
    return payload


def promote_advisory(data_root: Path, advisory_id: str) -> dict[str, Any]:
    if advisory_id.startswith("investigator-learning:"):
        return _promote_investigator_advisory(data_root, advisory_id)
    if advisory_id.startswith("mobile-learning:"):
        return _promote_mobile_advisory(data_root, advisory_id)
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
    if target.status != "candidate":
        raise ValueError("only candidate advisories can enter shadow status")
    shadow_count = sum(item.status == "shadow" for item in entries)
    if target.status == "candidate" and shadow_count >= MAX_SHADOW_ADVISORIES:
        raise ValueError("shadow learning advisory cap reached")
    now = datetime.now(timezone.utc)
    updated = [
        item.model_copy(update={"status": "shadow", "updated_at": now})
        if item.advisory_id == advisory_id else item
        for item in entries
    ]
    payload["generated_at"] = now.isoformat()
    payload["entries"] = [item.model_dump(mode="json") for item in updated]
    payload["active_advisories"] = [
        item.model_dump(mode="json") for item in updated if item.status == "active"
    ]
    payload["shadow_advisories"] = [
        item.model_dump(mode="json") for item in updated if item.status == "shadow"
    ]
    _write_json(memory_path, payload)
    _event(events_path, "advisory_shadowed", advisory_id, sample_size=target.sample_size)
    return payload


def activate_advisory(data_root: Path, advisory_id: str) -> dict[str, Any]:
    if advisory_id.startswith("investigator-learning:"):
        return _activate_investigator_advisory(data_root, advisory_id)
    if advisory_id.startswith("mobile-learning:"):
        return _activate_mobile_advisory(data_root, advisory_id)
    memory_path, events_path = _memory_paths(data_root)
    if not memory_path.exists():
        raise ValueError("learning memory does not exist; run learn first")
    payload = json.loads(memory_path.read_text())
    entries = [LearningAdvisory.model_validate(item) for item in payload.get("entries", [])]
    target = next((item for item in entries if item.advisory_id == advisory_id), None)
    if target is None or target.status != "shadow":
        raise ValueError("learning advisory must be in shadow status before activation")
    assessment = assess_shadow(data_root, target)
    if not assessment["no_degradation_passed"]:
        raise ValueError("learning advisory has not passed shadow no-degradation safeguards")
    if sum(item.status == "active" for item in entries) >= MAX_ACTIVE_ADVISORIES:
        raise ValueError("active learning advisory cap reached")
    now = datetime.now(timezone.utc)
    updated = [
        item.model_copy(update={
            "status": "active", "updated_at": now, "shadow_evaluation": assessment,
        }) if item.advisory_id == advisory_id else item
        for item in entries
    ]
    payload["generated_at"] = now.isoformat()
    payload["entries"] = [item.model_dump(mode="json") for item in updated]
    payload["active_advisories"] = [
        item.model_dump(mode="json") for item in updated if item.status == "active"
    ]
    payload["shadow_advisories"] = [
        item.model_dump(mode="json") for item in updated if item.status == "shadow"
    ]
    _write_json(memory_path, payload)
    _event(events_path, "advisory_activated", advisory_id,
           shadow_evaluation=assessment)
    return payload


def load_active_snapshot(data_root: Path, trade_date: date) -> dict[str, Any]:
    """Return an immutable, no-lookahead snapshot for packet construction."""
    memory_path, _ = _memory_paths(data_root)
    empty = {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "as_of": trade_date.isoformat(),
        "memory_sha256": "",
        "advisories": [],
        "mobile_advisories": [],
        "investigator_advisories": [],
    }
    if not memory_path.exists():
        return empty
    try:
        payload = json.loads(memory_path.read_text())
        memory_as_of = date.fromisoformat(payload["as_of"])
        advisories = [
            LearningAdvisory.model_validate(item)
            for field in ("active_advisories", "shadow_advisories")
            for item in payload.get(field, [])
        ]
        mobile_advisories = [
            MobileLearningAdvisory.model_validate(item)
            for field in ("active_mobile_advisories", "shadow_mobile_advisories")
            for item in payload.get(field, [])
        ]
        investigator_advisories = [
            InvestigatorLearningAdvisory.model_validate(item)
            for field in (
                "active_investigator_advisories", "shadow_investigator_advisories",
            )
            for item in payload.get(field, [])
        ]
    except (ValueError, KeyError, json.JSONDecodeError):
        return {**empty, "unavailable_reason": "learning memory is invalid"}
    if memory_as_of > trade_date:
        return {**empty, "unavailable_reason": "learning memory is newer than the packet date"}
    if len([item for item in advisories if item.status == "active"]) > MAX_ACTIVE_ADVISORIES \
            or len([item for item in advisories if item.status == "shadow"]) > MAX_SHADOW_ADVISORIES:
        return {**empty, "unavailable_reason": "learning advisory cap exceeded"}
    if len([item for item in mobile_advisories if item.status == "active"]) \
            > MAX_ACTIVE_MOBILE_ADVISORIES or len([
                item for item in mobile_advisories if item.status == "shadow"
            ]) > MAX_SHADOW_MOBILE_ADVISORIES:
        return {**empty, "unavailable_reason": "mobile learning advisory cap exceeded"}
    if len([item for item in investigator_advisories if item.status == "active"]) \
            > MAX_ACTIVE_INVESTIGATOR_ADVISORIES or len([
                item for item in investigator_advisories if item.status == "shadow"
            ]) > MAX_SHADOW_INVESTIGATOR_ADVISORIES:
        return {**empty, "unavailable_reason": "investigator learning advisory cap exceeded"}
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "as_of": memory_as_of.isoformat(),
        "memory_sha256": _hash(memory_path),
        "advisories": [item.model_dump(mode="json") for item in advisories],
        "mobile_advisories": [
            item.model_dump(mode="json") for item in mobile_advisories
        ],
        "investigator_advisories": [
            item.model_dump(mode="json") for item in investigator_advisories
        ],
    }


__all__ = [
    "CANDIDATE_MIN_SAMPLES", "MAX_ACTIVE_ADVISORIES",
    "MAX_ACTIVE_INVESTIGATOR_ADVISORIES", "MAX_ACTIVE_MOBILE_ADVISORIES",
    "MAX_ENTRIES", "MAX_INVESTIGATOR_ENTRIES", "MAX_MOBILE_ENTRIES",
    "MAX_SHADOW_ADVISORIES", "MAX_SHADOW_INVESTIGATOR_ADVISORIES",
    "MAX_SHADOW_MOBILE_ADVISORIES",
    "MIN_SHADOW_REVIEWS", "PROMOTION_MIN_SAMPLES",
    "activate_advisory", "assess_investigator_shadow", "assess_mobile_shadow", "assess_shadow",
    "build_memory", "load_active_snapshot",
    "promote_advisory",
]

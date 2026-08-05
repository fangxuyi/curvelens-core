"""Build framework-review evidence without mixing in product market learning."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

FRAMEWORK_REVIEW_SCHEMA_VERSION = 1
MAX_RUNS_PER_PRODUCT = 60
MIN_CROSS_PRODUCT_COUNT = 2
MIN_SINGLE_PRODUCT_OCCURRENCES = 3


def _load_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _trade_date(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.name.removeprefix("trade_date="))
    except ValueError:
        return None


def _manifest_schema(run_dir: Path) -> int:
    value = _load_object(run_dir / "manifest.json").get("schema_version")
    return value if isinstance(value, int) and value > 0 else 0


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def _component(event: dict[str, Any]) -> str:
    actor = str(event.get("actor") or "controller")
    phase = str(event.get("phase") or "")
    if actor in {"synthesis", "research_planner", "qc", "controller"}:
        return actor
    if phase == "RESEARCH_PLAN_REQUIRED":
        return "research_planner"
    if phase == "INVESTIGATORS_REQUIRED":
        return "investigator"
    return actor


def _normalize_failure(value: Any) -> str:
    text = " ".join(str(value or "unspecified validation failure").split())
    text = re.sub(r"\[\d+\]", "[]", text)
    text = re.sub(r"\b[0-9a-f]{16,64}\b", "<id>", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<date>", text)
    return text[:512]


def _status_counts(entries: Any) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, dict):
                counts[str(item.get("status") or "unknown")] += 1
    return dict(sorted(counts.items()))


def _memory_inventory(product: str, product_root: Path) -> dict[str, Any]:
    memory_path = product_root / "learning" / "memory.json"
    memory = _load_object(memory_path)
    return {
        "product": product,
        "memory_path": str(memory_path),
        "memory_present": bool(memory),
        "memory_as_of": memory.get("as_of") if memory else None,
        "families": {
            "forecast": _status_counts(memory.get("entries")),
            "mobile": _status_counts(memory.get("mobile_entries")),
            "investigator": _status_counts(memory.get("investigator_entries")),
        },
        "routing": "product_local_only",
        "framework_eligible": False,
        "reason": (
            "Market outcomes and advisory scopes remain product-local; they cannot alone "
            "justify a shared framework change."
        ),
    }


def _pattern_direction(family: str, item: dict[str, Any]) -> str:
    if family == "forecast":
        hit_rate = item.get("hit_rate")
        mean_brier = item.get("mean_brier")
        if isinstance(hit_rate, (int, float)) and isinstance(mean_brier, (int, float)):
            if hit_rate < 0.55 or mean_brier > 0.25:
                return "underperforming"
            if hit_rate >= 0.65 and mean_brier <= 0.20:
                return "comparatively_reliable"
        return "mixed"
    recommendation = str(item.get("recommendation") or "neutral")
    return recommendation if recommendation in {
        "prefer_select", "prefer_omit", "prefer_dispatch", "prefer_skip",
    } else "neutral"


def _learning_patterns(product: str, product_root: Path) -> list[dict[str, Any]]:
    memory = _load_object(product_root / "learning" / "memory.json")
    result = []
    for family, key in (
        ("forecast", "entries"),
        ("mobile", "mobile_entries"),
        ("investigator", "investigator_entries"),
    ):
        entries = memory.get(key)
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("scope"), dict):
                continue
            metrics = {name: item[name] for name in (
                "sample_size", "hit_rate", "mean_brier", "material_rate",
                "selection_accuracy", "missed_material_rate", "false_prominence_rate",
                "materiality_hit_rate", "lead_use_rate", "rejected_material_rate",
            ) if isinstance(item.get(name), (int, float))}
            result.append({
                "product": product,
                "family": family,
                "scope": item["scope"],
                "status": str(item.get("status") or "unknown"),
                "direction": _pattern_direction(family, item),
                "metrics": metrics,
            })
    return result


def _signal_id(component: str, normalized_failure: str) -> str:
    identity = f"{component}|{normalized_failure}".encode()
    return "framework-signal:" + hashlib.sha256(identity).hexdigest()[:20]


def build_framework_review_packet(
    products_root: Path, *, as_of: date, max_runs_per_product: int = MAX_RUNS_PER_PRODUCT,
) -> dict[str, Any]:
    """Aggregate shared workflow friction while preserving product-memory boundaries."""
    if max_runs_per_product < 1:
        raise ValueError("max_runs_per_product must be positive")
    products = [
        path for path in sorted(products_root.iterdir() if products_root.exists() else [])
        if path.is_dir() and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", path.name)
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    learning_grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    run_summaries = []
    inventories = []
    for product_root in products:
        product = product_root.name
        inventories.append(_memory_inventory(product, product_root))
        for pattern in _learning_patterns(product, product_root):
            scope_key = json.dumps(pattern["scope"], sort_keys=True, separators=(",", ":"))
            learning_grouped[(pattern["family"], scope_key)].append(pattern)
        workflow_root = product_root / "analysis_workflow"
        dated_runs = [
            (run_date, path)
            for path in workflow_root.glob("trade_date=*")
            if (run_date := _trade_date(path)) is not None and run_date <= as_of
        ]
        dated_runs.sort(key=lambda item: item[0])
        dated_runs = dated_runs[-max_runs_per_product:]
        schemas = {path: _manifest_schema(path) for _, path in dated_runs}
        latest_schema = max(schemas.values(), default=0)
        product_counts: Counter[str] = Counter()
        current_schema_runs = 0
        for run_date, run_dir in dated_runs:
            schema_version = schemas[run_dir]
            events_path = run_dir / "workflow_events.jsonl"
            events = _events(events_path)
            product_counts.update(str(item.get("event") or "unknown") for item in events)
            run = _load_object(run_dir / "run.json")
            if schema_version != latest_schema:
                continue
            current_schema_runs += 1
            for event in events:
                if event.get("event") != "validation_rejected":
                    continue
                component = _component(event)
                normalized = _normalize_failure(event.get("detail"))
                grouped[(component, normalized)].append({
                    "product": product,
                    "trade_date": run_date.isoformat(),
                    "schema_version": schema_version,
                    "event": "validation_rejected",
                    "detail": str(event.get("detail") or ""),
                    "source_path": str(events_path),
                })
            if run.get("phase") == "BLOCKED":
                normalized = _normalize_failure(run.get("block_reason"))
                grouped[("controller", normalized)].append({
                    "product": product,
                    "trade_date": run_date.isoformat(),
                    "schema_version": schema_version,
                    "event": "run_blocked",
                    "detail": str(run.get("block_reason") or ""),
                    "source_path": str(run_dir / "run.json"),
                })
        run_summaries.append({
            "product": product,
            "runs_considered": len(dated_runs),
            "latest_packet_schema": latest_schema,
            "current_schema_runs": current_schema_runs,
            "event_counts": dict(sorted(product_counts.items())),
        })

    signals = []
    for (component, normalized), occurrences in grouped.items():
        products_seen = sorted({item["product"] for item in occurrences})
        if len(products_seen) >= MIN_CROSS_PRODUCT_COUNT:
            routing = "framework_candidate"
            reason = "The same current-schema workflow failure recurred across products."
        elif len(occurrences) >= MIN_SINGLE_PRODUCT_OCCURRENCES:
            routing = "shared_component_review"
            reason = (
                "The failure repeatedly affected one product in a shared component; review is "
                "required before deciding whether it is product-specific or generalizable."
            )
        else:
            routing = "insufficient_evidence"
            reason = "The current evidence is too narrow for a shared framework proposal."
        signals.append({
            "signal_id": _signal_id(component, normalized),
            "component": component,
            "normalized_failure": normalized,
            "occurrence_count": len(occurrences),
            "product_count": len(products_seen),
            "products": products_seen,
            "routing": routing,
            "framework_eligible": routing != "insufficient_evidence",
            "reason": reason,
            "occurrences": sorted(
                occurrences, key=lambda item: (item["trade_date"], item["product"])
            )[-20:],
        })
    signals.sort(key=lambda item: (
        not item["framework_eligible"], -item["product_count"],
        -item["occurrence_count"], item["signal_id"],
    ))
    learning_signals = []
    for (family, scope_key), occurrences in learning_grouped.items():
        products_seen = sorted({item["product"] for item in occurrences})
        directions = sorted({item["direction"] for item in occurrences})
        consistent = len(directions) == 1 and directions[0] not in {"mixed", "neutral"}
        framework_eligible = (
            len(products_seen) >= MIN_CROSS_PRODUCT_COUNT and consistent
        )
        routing = "framework_candidate" if framework_eligible else "product_local_only"
        reason = (
            "The same bounded learning scope has a consistent non-neutral outcome pattern "
            "across products."
            if framework_eligible else
            "The pattern is product-specific, mixed, neutral, or lacks cross-product support."
        )
        identity = f"learning|{family}|{scope_key}|{'|'.join(directions)}"
        learning_signals.append({
            "signal_id": "framework-learning-signal:" + hashlib.sha256(
                identity.encode()
            ).hexdigest()[:20],
            "family": family,
            "scope": json.loads(scope_key),
            "directions": directions,
            "product_count": len(products_seen),
            "products": products_seen,
            "routing": routing,
            "framework_eligible": framework_eligible,
            "reason": reason,
            "occurrences": sorted(occurrences, key=lambda item: item["product"]),
        })
    learning_signals.sort(key=lambda item: (
        not item["framework_eligible"], -item["product_count"], item["signal_id"],
    ))
    body = {
        "schema_version": FRAMEWORK_REVIEW_SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "scope": "one repository-wide review across visible product runtimes",
        "classification_policy": {
            "product_local": (
                "Narrative observations and adjustments remain product-local. Structured scopes "
                "and outcome metrics can support framework review only when the same non-neutral "
                "pattern appears in at least two products."
            ),
            "framework_candidate": (
                "Requires the same current-schema workflow failure in at least two products."
            ),
            "shared_component_review": (
                "Requires at least three current-schema occurrences in one product and still "
                "requires reviewer classification."
            ),
            "insufficient_evidence": "Cannot produce a framework change suggestion.",
        },
        "products_scanned": [path.name for path in products],
        "product_learning_inventory": inventories,
        "run_summaries": run_summaries,
        "workflow_signals": signals,
        "learning_pattern_signals": learning_signals,
        "summary": {
            "product_count": len(products),
            "workflow_signal_count": len(signals),
            "learning_pattern_count": len(learning_signals),
            "framework_eligible_count": sum(
                bool(item["framework_eligible"]) for item in signals
            ) + sum(bool(item["framework_eligible"]) for item in learning_signals),
        },
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {
        **body,
        "packet_id": hashlib.sha256(canonical).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_framework_review_packet(
    products_root: Path, output_path: Path, *, as_of: date,
    max_runs_per_product: int = MAX_RUNS_PER_PRODUCT,
) -> dict[str, Any]:
    packet = build_framework_review_packet(
        products_root, as_of=as_of, max_runs_per_product=max_runs_per_product,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(f".{output_path.name}.tmp")
    temp.write_text(json.dumps(packet, indent=2))
    temp.replace(output_path)
    return packet


__all__ = [
    "FRAMEWORK_REVIEW_SCHEMA_VERSION", "MAX_RUNS_PER_PRODUCT",
    "build_framework_review_packet", "write_framework_review_packet",
]

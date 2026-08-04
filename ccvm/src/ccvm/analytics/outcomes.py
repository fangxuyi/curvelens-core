"""Deterministic realized outcomes for forecast-ledger records."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ccvm.reference.exchange_calendar import is_business_day
from ccvm.schemas.learning import OutcomeMetric, OutcomeRecord, OutcomeRule


BusinessDayFn = Callable[[date], bool]
_REPORT_METRICS = ("front_settlement", "front_atm_iv")


def nth_future_business_session(
    trade_date: date,
    horizon_sessions: int,
    *,
    business_day_fn: BusinessDayFn | None = None,
) -> date:
    """Return the Nth CME session strictly after ``trade_date``."""
    if not isinstance(trade_date, date) or isinstance(trade_date, datetime):
        raise TypeError("trade_date must be a date")
    if isinstance(horizon_sessions, bool) or not isinstance(horizon_sessions, int):
        raise TypeError("horizon_sessions must be an integer")
    if horizon_sessions < 1:
        raise ValueError("horizon_sessions must be positive")

    is_session = business_day_fn or is_business_day
    current = trade_date
    sessions = 0
    while sessions < horizon_sessions:
        current += timedelta(days=1)
        if is_session(current):
            sessions += 1
    if current <= trade_date:
        raise AssertionError("future session calculation leaked the source date")
    return current


def compute_target_date(
    trade_date: date,
    horizon_sessions: int,
    *,
    business_day_fn: BusinessDayFn | None = None,
) -> date:
    """Alias with a concise name for callers building outcome records."""
    return nth_future_business_session(
        trade_date, horizon_sessions, business_day_fn=business_day_fn
    )


def _as_date(value: date | str, field_name: str) -> date:
    if isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a date, not datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be ISO YYYY-MM-DD") from exc
    raise TypeError(f"{field_name} must be a date or ISO date string")


def _report_path(reports_dir: Path, report_date: date) -> Path:
    return Path(reports_dir) / f"{report_date.isoformat()}.json"


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _load_report_snapshot(reports_dir: Path, report_date: date) -> dict[str, Any]:
    path = _report_path(reports_dir, report_date)
    result: dict[str, Any] = {
        "path": path,
        "sha256": None,
        "metrics": {metric: None for metric in _REPORT_METRICS},
        "notes": [],
        "exists": path.exists(),
    }
    if not path.exists():
        result["notes"].append(f"report missing: {path.name}")
        return result
    try:
        raw = path.read_bytes()
        result["sha256"] = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result["notes"].append(f"report malformed: {path.name} ({type(exc).__name__})")
        return result
    if not isinstance(payload, Mapping):
        result["notes"].append(f"report malformed: {path.name} (root is not an object)")
        return result
    if payload.get("trade_date") != report_date.isoformat():
        result["notes"].append(f"report trade_date mismatch: {path.name}")
        return result

    sections = payload.get("sections")
    market_risk = sections.get("market_risk") if isinstance(sections, Mapping) else None
    futures = market_risk.get("futures") if isinstance(market_risk, Mapping) else None
    options = market_risk.get("options") if isinstance(market_risk, Mapping) else None
    raw_values = {
        "front_settlement": futures.get("front_settlement") if isinstance(futures, Mapping) else None,
        "front_atm_iv": options.get("atm_iv") if isinstance(options, Mapping) else None,
    }
    for metric, raw_value in raw_values.items():
        if raw_value is None:
            result["notes"].append(f"{metric} missing in {path.name}")
            continue
        value = _finite_number(raw_value)
        if value is None:
            result["notes"].append(f"{metric} malformed or nonfinite in {path.name}")
            continue
        result["metrics"][metric] = value
    return result


def load_report_metrics(reports_dir: Path, report_date: date | str) -> dict[str, float | None]:
    """Load the two supported report metrics without inventing missing values."""
    snapshot = _load_report_snapshot(Path(reports_dir), _as_date(report_date, "report_date"))
    return dict(snapshot["metrics"])


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _input_hash(value: Path | bytes | Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    if isinstance(value, Mapping):
        return _canonical_hash(value)
    path = Path(value)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _analysis_trade_date(analysis_path: Path | None) -> date | None:
    if analysis_path is None or not analysis_path.exists():
        return None
    try:
        payload = json.loads(analysis_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, Mapping) and payload.get("trade_date") is not None:
        try:
            return _as_date(payload["trade_date"], "trade_date")
        except (TypeError, ValueError):
            return None
    return None


def _forecast_value(forecast: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(forecast, Mapping):
        return forecast.get(key, default)
    return getattr(forecast, key, default)


def _contract_dimension(
    forecast_contract: Mapping[str, Any], dimension: str
) -> tuple[str, OutcomeRule, Mapping[str, Any]]:
    dimensions = forecast_contract.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise ValueError("forecast_contract.dimensions must be an object")
    config = dimensions.get(dimension)
    if not isinstance(config, Mapping):
        raise ValueError(f"forecast dimension is not configured: {dimension!r}")
    metric_key = config.get("metric_key")
    if not isinstance(metric_key, str) or not metric_key.strip():
        raise ValueError(f"forecast dimension {dimension!r} has no metric_key")
    try:
        rule = OutcomeRule.model_validate(config.get("outcome_rule"))
    except Exception as exc:
        raise ValueError(f"invalid outcome_rule for dimension {dimension!r}: {exc}") from exc
    configured_labels = config.get("labels")
    if configured_labels != rule.labels:
        raise ValueError(
            f"forecast dimension {dimension!r} labels do not match its outcome_rule"
        )
    return metric_key, rule, config


def apply_outcome_rule(
    baseline: float,
    target: float,
    rule: OutcomeRule | Mapping[str, Any],
) -> tuple[float, str]:
    """Apply a validated generic rule and return ``(change, realized_label)``."""
    base = _finite_number(baseline)
    end = _finite_number(target)
    if base is None or end is None:
        raise ValueError("baseline and target must be finite numbers")
    parsed = rule if isinstance(rule, OutcomeRule) else OutcomeRule.model_validate(rule)
    if parsed.calculation in {"return", "absolute_return"}:
        if base == 0:
            raise ValueError("return calculation requires a nonzero baseline")
        change = (end - base) / base
        if parsed.calculation == "absolute_return":
            change = abs(change)
    else:
        change = end - base

    if parsed.kind == "signed_band":
        threshold = parsed.thresholds[0]
        if change <= -threshold:
            label = parsed.labels[0]
        elif change >= threshold:
            label = parsed.labels[2]
        else:
            label = parsed.labels[1]
    else:
        magnitude = abs(change)
        if magnitude < parsed.thresholds[0]:
            label = parsed.labels[0]
        elif magnitude < parsed.thresholds[1]:
            label = parsed.labels[1]
        else:
            label = parsed.labels[2]
    return change, label


def realize_outcome(
    forecast: Mapping[str, Any] | Any,
    forecast_contract: Mapping[str, Any],
    trade_date: date | str | None,
    reports_dir: Path,
    *,
    analysis_path: Path | None = None,
    analysis_input: bytes | Mapping[str, Any] | None = None,
    policy_version: int | None = None,
    generated_at: datetime | None = None,
    business_day_fn: BusinessDayFn | None = None,
) -> OutcomeRecord:
    """Build one realized record for a forecast-ledger item."""
    source_date = (
        _as_date(trade_date, "trade_date") if trade_date is not None
        else _analysis_trade_date(analysis_path)
    )
    if source_date is None:
        source_from_forecast = _forecast_value(forecast, "source_trade_date")
        if source_from_forecast is None:
            source_from_forecast = _forecast_value(forecast, "trade_date")
        if source_from_forecast is None:
            raise ValueError("trade_date is required")
        source_date = _as_date(source_from_forecast, "trade_date")

    dimension = _forecast_value(forecast, "dimension")
    if not isinstance(dimension, str) or not dimension.strip():
        raise ValueError("forecast dimension is required")
    metric_key, rule, dimension_config = _contract_dimension(forecast_contract, dimension)
    horizon = _forecast_value(forecast, "horizon_sessions")
    if horizon is None:
        horizon = _forecast_value(forecast, "horizon")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("forecast horizon_sessions must be a positive integer")
    target_date = nth_future_business_session(
        source_date, horizon, business_day_fn=business_day_fn
    )

    baseline = _load_report_snapshot(Path(reports_dir), source_date)
    target = _load_report_snapshot(Path(reports_dir), target_date)
    source_metric = rule.source_metric
    baseline_value = baseline["metrics"].get(source_metric)
    target_value = target["metrics"].get(source_metric)
    notes = [f"baseline: {note}" for note in baseline["notes"]]
    notes.extend(f"target: {note}" for note in target["notes"])

    change: float | None = None
    realized_label: str | None = None
    if baseline_value is not None and target_value is not None:
        try:
            change, realized_label = apply_outcome_rule(baseline_value, target_value, rule)
        except ValueError as exc:
            notes.append(f"outcome unavailable: {exc}")

    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    target_available = target_value is not None and change is not None and realized_label is not None
    if target_available and baseline_value is not None:
        status = "complete"
    elif baseline_value is not None and target_date > now.date() and not target["exists"]:
        status = "pending"
        notes.append(f"target report is pending for {target_date.isoformat()}")
    else:
        status = "missing"

    analysis_sha256 = _input_hash(analysis_path or analysis_input)
    if analysis_sha256 is None:
        notes.append("analysis input hash unavailable")
    policy = dict(forecast_contract)
    policy_version_value = policy_version if policy_version is not None else policy.get("version")
    if isinstance(policy_version_value, bool) or not isinstance(policy_version_value, int) or policy_version_value < 1:
        raise ValueError("policy_version must be a positive integer")
    forecast_id = _forecast_value(forecast, "forecast_id")
    if not isinstance(forecast_id, str) or not forecast_id.strip():
        raise ValueError("forecast_id is required")
    record = OutcomeRecord(
        forecast_id=forecast_id,
        dimension=dimension,
        expected_label=_forecast_value(forecast, "expected_label"),
        source_trade_date=source_date,
        target_date=target_date,
        horizon_sessions=horizon,
        status=status,
        metrics=[OutcomeMetric(
            metric_key=metric_key,
            baseline=baseline_value,
            target=target_value,
            change=change,
            realized_label=realized_label,
        )],
        data_quality_notes=notes,
        analysis_sha256=analysis_sha256,
        baseline_sha256=baseline["sha256"],
        target_sha256=target["sha256"],
        policy_sha256=_canonical_hash({"version": policy_version_value, "dimension": dimension, "config": dimension_config}),
        policy_version=policy_version_value,
        generated_at=now,
        record_version=1,
    )
    return record


def build_outcome_record(*args: Any, **kwargs: Any) -> OutcomeRecord:
    """Descriptive alias for callers that prefer a builder name."""
    return realize_outcome(*args, **kwargs)


def _write_json_atomic(path: Path, payload: bytes) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_bytes(payload)
    temp.replace(path)


def _record_identity(record: OutcomeRecord) -> str:
    payload = record.model_dump(mode="json", exclude={
        "generated_at", "record_version", "supersedes_hash",
    })
    return _canonical_hash(payload)


def persist_outcome(record: OutcomeRecord, path: Path) -> OutcomeRecord:
    """Persist a record idempotently, archiving changed current records."""
    current_path = Path(path)
    current_path.parent.mkdir(parents=True, exist_ok=True)
    record = record if isinstance(record, OutcomeRecord) else OutcomeRecord.model_validate(record)
    current_bytes = json.dumps(
        record.model_dump(mode="json"), sort_keys=True, indent=2, separators=(",", ":")
    ).encode("utf-8")
    if current_path.exists():
        old_bytes = current_path.read_bytes()
        try:
            old = OutcomeRecord.model_validate_json(old_bytes)
        except Exception:
            old = None
        if old is not None and _record_identity(old) == _record_identity(record):
            return old
        old_version = old.record_version if old is not None else 1
        versions_dir = current_path.parent / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        archive = versions_dir / f"{current_path.stem}.v{old_version}{current_path.suffix}"
        if archive.exists():
            archive = versions_dir / (
                f"{current_path.stem}.v{old_version}."
                f"{hashlib.sha256(old_bytes).hexdigest()[:12]}{current_path.suffix}"
            )
        archive.write_bytes(old_bytes)
        record = record.model_copy(update={
            "record_version": old_version + 1,
            "supersedes_hash": hashlib.sha256(old_bytes).hexdigest(),
        })
        current_bytes = json.dumps(
            record.model_dump(mode="json"), sort_keys=True, indent=2, separators=(",", ":")
        ).encode("utf-8")
    _write_json_atomic(current_path, current_bytes)
    return record


def persist_outcome_record(record: OutcomeRecord, path: Path) -> OutcomeRecord:
    """Alias for :func:`persist_outcome`."""
    return persist_outcome(record, path)


__all__ = [
    "apply_outcome_rule",
    "build_outcome_record",
    "compute_target_date",
    "load_report_metrics",
    "nth_future_business_session",
    "persist_outcome",
    "persist_outcome_record",
    "realize_outcome",
]

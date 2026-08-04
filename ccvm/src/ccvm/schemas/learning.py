"""Product-neutral contracts for ex-ante and realized learning records."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SAFE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"
_PLACEHOLDER_VALUES = frozenset({
    "", "-", "--", "?", "na", "n/a", "n.a.", "none", "null", "nil",
    "unknown", "unk", "tbd", "todo", "placeholder", "not applicable",
    "not_applicable", "not available", "not_available",
})
_MAX_EVIDENCE_IDS = 64
_MAX_HORIZON_SESSIONS = 252
_SAFE_IDENTIFIER_RE = re.compile(_SAFE_IDENTIFIER_PATTERN)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SafeKey = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=_SAFE_KEY_PATTERN),
]
SafeIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=_SAFE_IDENTIFIER_PATTERN),
]


def _validate_text(value: str, field_name: str) -> str:
    """Reject values that cannot be safely retained as structured records."""
    if not value or value != value.strip() or not value.isprintable():
        raise ValueError(f"{field_name} must be nonempty printable text without surrounding whitespace")
    if value.casefold() in _PLACEHOLDER_VALUES:
        raise ValueError(f"{field_name} cannot be a placeholder value")
    return value


def _validate_identifier(value: str, field_name: str) -> str:
    value = _validate_text(value, field_name)
    if not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field_name} must use a safe identifier format")
    if any(part in {"", ".", ".."} for part in re.split(r"[:/]", value)):
        raise ValueError(f"{field_name} contains an unsafe path segment")
    return value


class _LearningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator(
        "forecast_id", "advisory_id", "dimension", "metric_key",
        "expected_label", "rationale", check_fields=False,
    )
    @classmethod
    def reject_invalid_text(cls, value: str, info) -> str:
        return _validate_text(value, info.field_name)

    @field_validator("evidence_ids", check_fields=False)
    @classmethod
    def validate_evidence_ids(cls, value: list[str], info) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicate evidence IDs")
        return [_validate_identifier(item, info.field_name) for item in value]


class ForecastLedgerItem(_LearningModel):
    """A falsifiable forecast recorded before its outcome is known."""

    forecast_id: SafeIdentifier
    source_view_rank: Annotated[int, Field(strict=True, ge=1, le=3)]
    dimension: SafeKey
    metric_key: SafeKey
    horizon_sessions: Annotated[
        int,
        Field(strict=True, gt=0, le=_MAX_HORIZON_SESSIONS),
    ]
    expected_label: SafeKey
    confidence: Literal["high", "medium", "low"]
    evidence_ids: Annotated[
        list[SafeIdentifier],
        Field(min_length=1, max_length=_MAX_EVIDENCE_IDS),
    ]


class MemoryFeedbackItem(_LearningModel):
    """The ex-ante disposition and rationale for a supplied advisory."""

    advisory_id: SafeIdentifier
    disposition: Literal["used", "rejected"]
    rationale: Annotated[str, Field(min_length=1, max_length=2048)]
    evidence_ids: Annotated[
        list[SafeIdentifier],
        Field(max_length=_MAX_EVIDENCE_IDS),
    ]


class OutcomeRule(_LearningModel):
    """A generic deterministic rule for converting a metric change to a label."""

    source_metric: Literal["front_settlement", "front_atm_iv"]
    calculation: Literal["return", "change", "absolute_return"]
    kind: Literal["signed_band", "absolute_bands"]
    thresholds: Annotated[list[float], Field(min_length=1, max_length=2)]
    labels: Annotated[list[str], Field(min_length=3, max_length=3)]

    @field_validator("thresholds")
    @classmethod
    def validate_thresholds(cls, value: list[float]) -> list[float]:
        if any(not math.isfinite(item) or item <= 0 for item in value):
            raise ValueError("thresholds must contain only positive finite numbers")
        return value

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: list[str]) -> list[str]:
        cleaned = [_validate_text(item, "labels") for item in value]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("labels must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_shape(self) -> OutcomeRule:
        expected = 1 if self.kind == "signed_band" else 2
        if len(self.thresholds) != expected:
            raise ValueError(
                f"{self.kind} requires exactly {expected} threshold(s)"
            )
        if self.kind == "absolute_bands" and self.thresholds[0] >= self.thresholds[1]:
            raise ValueError("absolute_bands thresholds must be strictly ascending")
        return self


class OutcomeMetric(_LearningModel):
    """One metric's baseline, target, change, and deterministic label."""

    metric_key: SafeKey
    baseline: float | None = None
    target: float | None = None
    change: float | None = None
    realized_label: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("baseline", "target", "change")
    @classmethod
    def validate_finite_metric(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("metric values must be finite when present")
        return value

    @field_validator("realized_label")
    @classmethod
    def validate_realized_label(cls, value: str | None) -> str | None:
        return None if value is None else _validate_text(value, "realized_label")


class OutcomeRecord(_LearningModel):
    """Strict, auditable realization of one forecast-ledger item."""

    forecast_id: SafeIdentifier
    dimension: SafeKey
    expected_label: str | None = Field(default=None, min_length=1, max_length=128)
    source_trade_date: date
    target_date: date
    horizon_sessions: Annotated[
        int,
        Field(strict=True, gt=0, le=_MAX_HORIZON_SESSIONS),
    ]
    status: Literal["pending", "missing", "complete"]
    metrics: Annotated[list[OutcomeMetric], Field(min_length=1, max_length=64)]
    data_quality_notes: Annotated[list[str], Field(max_length=128)] = Field(default_factory=list)
    analysis_sha256: str | None = None
    baseline_sha256: str | None = None
    target_sha256: str | None = None
    policy_sha256: str | None = None
    policy_version: Annotated[int, Field(strict=True, ge=1)]
    generated_at: datetime
    record_version: Annotated[int, Field(strict=True, ge=1)]
    supersedes_hash: str | None = None

    @field_validator("expected_label")
    @classmethod
    def validate_expected_label(cls, value: str | None) -> str | None:
        return None if value is None else _validate_text(value, "expected_label")

    @field_validator("data_quality_notes")
    @classmethod
    def validate_notes(cls, value: list[str]) -> list[str]:
        return [_validate_text(item, "data_quality_notes") for item in value]

    @field_validator(
        "analysis_sha256", "baseline_sha256", "target_sha256",
        "policy_sha256", "supersedes_hash",
    )
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("hashes must be lowercase SHA256 hex digests")
        return value

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_dates_and_completion(self) -> OutcomeRecord:
        if self.target_date <= self.source_trade_date:
            raise ValueError("target_date must be later than source_trade_date")
        if self.status == "complete":
            if any(
                item.baseline is None
                or item.target is None
                or item.change is None
                or item.realized_label is None
                for item in self.metrics
            ):
                raise ValueError("complete outcomes require complete metric values")
        return self


class EvaluationRecord(_LearningModel):
    """Deterministic scoring of one forecast against one realized outcome."""

    schema_version: Literal[1] = 1
    forecast_id: SafeIdentifier
    dimension: SafeKey
    horizon_sessions: Annotated[int, Field(strict=True, gt=0, le=_MAX_HORIZON_SESSIONS)]
    source_view_rank: Annotated[int, Field(strict=True, ge=1, le=3)]
    confidence: Literal["high", "medium", "low"]
    expected_label: SafeKey
    realized_label: SafeKey | None = None
    status: Literal["scored", "unscored"]
    unscored_reason: Annotated[str, Field(max_length=512)] = ""
    hit: bool | None = None
    confidence_probability: Annotated[float | None, Field(ge=0, le=1)] = None
    brier_loss: Annotated[float | None, Field(ge=0, le=1)] = None
    rank_weight: Annotated[float, Field(gt=0, le=1)]
    weighted_hit: Annotated[float | None, Field(ge=0, le=1)] = None
    association: Literal["forecast_associated_with_realized_outcome"] = (
        "forecast_associated_with_realized_outcome"
    )
    analysis_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    outcome_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    evaluated_at: datetime
    evaluator_version: Annotated[int, Field(strict=True, gt=0)] = 1

    @model_validator(mode="after")
    def validate_score_state(self) -> EvaluationRecord:
        scores = (
            self.realized_label, self.hit, self.confidence_probability,
            self.brier_loss, self.weighted_hit,
        )
        if self.status == "scored" and any(value is None for value in scores):
            raise ValueError("scored evaluation requires realized label and numeric scores")
        if self.status == "unscored" and not self.unscored_reason:
            raise ValueError("unscored evaluation requires a reason")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return self


class LearningScope(_LearningModel):
    """Stable, product-neutral fields allowed to key a learning advisory."""

    dimension: SafeKey
    horizon_sessions: Annotated[int, Field(strict=True, gt=0, le=_MAX_HORIZON_SESSIONS)]
    confidence: Literal["high", "medium", "low"]


class LearningAdvisory(_LearningModel):
    """Bounded aggregate memory; never a new source of market evidence."""

    advisory_id: SafeIdentifier
    status: Literal["candidate", "active", "retired"]
    scope: LearningScope
    observation: Annotated[str, Field(min_length=1, max_length=512)]
    suggested_adjustment: Annotated[str, Field(min_length=1, max_length=512)]
    sample_size: Annotated[int, Field(strict=True, ge=1)]
    hits: Annotated[int, Field(strict=True, ge=0)]
    hit_rate: Annotated[float, Field(ge=0, le=1)]
    mean_brier: Annotated[float, Field(ge=0, le=1)]
    promotion_eligible: bool
    source_evaluation_sha256: Annotated[
        list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]],
        Field(min_length=1, max_length=252),
    ]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_advisory_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("learning advisory timestamps must be timezone-aware")
        return value


__all__ = [
    "ForecastLedgerItem",
    "MemoryFeedbackItem",
    "OutcomeRule",
    "OutcomeMetric",
    "OutcomeRecord",
    "EvaluationRecord",
    "LearningScope",
    "LearningAdvisory",
]

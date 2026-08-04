"""Product-neutral contracts for ex-ante learning records."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


__all__ = ["ForecastLedgerItem", "MemoryFeedbackItem"]

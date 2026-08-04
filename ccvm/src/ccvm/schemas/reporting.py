"""Strict contracts for delivery-facing report selection."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MobileSelectionCandidate(BaseModel):
    """An ex-ante decision about one validated full-report view."""

    model_config = ConfigDict(extra="forbid")

    source_view_rank: Annotated[int, Field(strict=True, ge=1, le=3)]
    disposition: Literal["selected", "omitted"]
    materiality: Literal["high", "medium", "low"]
    expected_impact_dimensions: Annotated[
        list[str], Field(min_length=1, max_length=3)
    ]
    rationale: Annotated[str, Field(min_length=1, max_length=512)]
    evidence_ids: Annotated[list[str], Field(min_length=1, max_length=64)]

    @field_validator("expected_impact_dimensions", "evidence_ids")
    @classmethod
    def require_unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value

    @field_validator("rationale")
    @classmethod
    def require_clean_rationale(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("rationale must be clean printable text")
        return value


class MobileSelection(BaseModel):
    """A complete, auditable mobile editorial decision."""

    model_config = ConfigDict(extra="forbid")

    selected_view_ranks: Annotated[list[int], Field(min_length=1, max_length=2)]
    selection_rationale: Annotated[str, Field(min_length=1, max_length=512)]
    candidates: Annotated[list[MobileSelectionCandidate], Field(min_length=3, max_length=3)]
    limitation_disposition: Literal["included", "omitted", "not_applicable"]
    limitation_rationale: Annotated[str, Field(min_length=1, max_length=512)]

    @field_validator("selection_rationale", "limitation_rationale")
    @classmethod
    def require_clean_rationale(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("rationale must be clean printable text")
        return value

    @model_validator(mode="after")
    def validate_selection(self) -> MobileSelection:
        if len(self.selected_view_ranks) != len(set(self.selected_view_ranks)):
            raise ValueError("selected_view_ranks must be unique")
        if [item.source_view_rank for item in self.candidates] != [1, 2, 3]:
            raise ValueError("candidates must preserve source ranks 1, 2, and 3")
        selected = [
            item.source_view_rank for item in self.candidates
            if item.disposition == "selected"
        ]
        if selected != self.selected_view_ranks:
            raise ValueError("selected_view_ranks must match selected candidates in rank order")
        if len(selected) == 2 and self.candidates[selected[1] - 1].materiality == "low":
            raise ValueError("a second mobile view cannot have low expected materiality")
        return self


__all__ = ["MobileSelection", "MobileSelectionCandidate"]

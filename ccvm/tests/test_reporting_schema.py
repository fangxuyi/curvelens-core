import pytest
from pydantic import ValidationError

from ccvm.schemas.reporting import MobileSelection


def _selection(**overrides):
    value = {
        "selected_view_ranks": [1],
        "selection_rationale": "The first view has the clearest next-session impact.",
        "candidates": [{
            "source_view_rank": rank,
            "disposition": "selected" if rank == 1 else "omitted",
            "materiality": "high" if rank == 1 else "low",
            "expected_impact_dimensions": ["price_direction"],
            "rationale": "The view was classified using current validated evidence.",
            "evidence_ids": [f"feature:view-{rank}"],
        } for rank in (1, 2, 3)],
        "limitation_disposition": "not_applicable",
        "limitation_rationale": "The synthesis reported no material data limitation.",
    }
    value.update(overrides)
    return value


def test_mobile_selection_requires_complete_ranked_candidate_decision():
    selection = MobileSelection.model_validate(_selection())

    assert selection.selected_view_ranks == [1]
    assert [item.source_view_rank for item in selection.candidates] == [1, 2, 3]


def test_mobile_selection_rejects_mismatched_selected_ranks():
    with pytest.raises(ValidationError, match="must match selected candidates"):
        MobileSelection.model_validate(_selection(selected_view_ranks=[2]))


def test_mobile_selection_rejects_low_materiality_second_item():
    value = _selection(selected_view_ranks=[1, 2])
    value["candidates"][1]["disposition"] = "selected"

    with pytest.raises(ValidationError, match="second mobile view"):
        MobileSelection.model_validate(value)


def test_mobile_selection_forbids_unexpected_fields():
    with pytest.raises(ValidationError):
        MobileSelection.model_validate(_selection(unexpected=True))


def test_mobile_selection_rejects_blank_top_level_rationale():
    with pytest.raises(ValidationError, match="clean printable text"):
        MobileSelection.model_validate(_selection(selection_rationale="   "))

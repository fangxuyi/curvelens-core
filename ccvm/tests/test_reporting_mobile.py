from ccvm.reporting.mobile import MAX_MOBILE_BRIEF_CHARS, render_mobile_brief


def _metric(label: str, value: str) -> dict:
    return {"label": label, "value": value}


def _view(rank: int, *, relationship: str = "cross_supported") -> dict:
    return {
        "rank": rank,
        "title": f"Complete view {rank}",
        "plain_english_view": (
            "This complete opening sentence carries the decision. "
            "This second sentence is deliberately too long to survive the local mobile limit "
            + "without being cut into a fragment " * 12
            + "."
        ),
        "key_metrics": [
            _metric("Front settlement", "$80.34/bbl, down 5.11%"),
            _metric("M1-M3 spread", "$4.66/bbl backwardation"),
        ],
        "evidence_relationship": relationship,
        "conflicting_evidence": ([{"claim": "The conflicting signal remains material."}]
                                 if relationship == "conflicting" else []),
        "driver_analysis": {
            "status": "partially_supported",
            "explanation": "Driver evidence partly explains the move.",
        },
        "story_chain": {
            "observed_move": {"claim": "The front settlement changed materially."},
            "narrative_change": {
                "status": "partially_supported",
                "claim": "A dated catalyst changed the forward narrative.",
            },
            "option_market_readthrough": {
                "status": "conflicted",
                "claim": "Options did not fully confirm the price move.",
            },
            "forward_watch": {
                "claim": "Watch the next official release for confirmation.",
            },
        },
        "what_to_watch": ["Watch whether the next settlement confirms the move."],
    }


def test_mobile_renderer_keeps_complete_prose_sentences():
    analysis = {
        "product": "wti",
        "trade_date": "2026-08-04",
        "synthesis": {
            "plain_english_summary": (
                "WTI repriced lower while prompt structure stayed tight. "
                "This deliberately oversized follow-up should be omitted instead of being cut "
                + "mid-sentence " * 30
                + "."
            ),
            "top_views": [_view(1), _view(2), _view(3)],
            "mobile_selection": {
                "selected_view_ranks": [1],
                "limitation_disposition": "included",
            },
            "data_limitations": ["Prices are end-of-day settlements, not executable quotes."],
        },
    }

    mobile = render_mobile_brief(analysis)

    assert "WTI repriced lower while prompt structure stayed tight." in mobile
    assert "This complete opening sentence carries the decision." in mobile
    assert "Why it moved: A dated catalyst changed the forward narrative." in mobile
    assert "Options: Options did not fully confirm the price move." in mobile
    assert "Watch: Watch the next official release for confirmation." in mobile
    assert "oversized follow-up" not in mobile
    assert "without being cut into a fragment" not in mobile
    assert "…" not in mobile
    assert len(mobile) <= MAX_MOBILE_BRIEF_CHARS


def test_mobile_renderer_drops_whole_optional_lines_to_fit_budget():
    views = [_view(1, relationship="conflicting"), _view(2), _view(3)]
    for view in views:
        view["plain_english_view"] = "A complete view sentence " + "with context " * 12 + "."
        view["what_to_watch"] = ["A complete watch sentence " + "with detail " * 12 + "."]
    analysis = {
        "product": "gold",
        "trade_date": "2026-08-04",
        "synthesis": {
            "plain_english_summary": "A complete bottom-line sentence " + "with context " * 15 + ".",
            "top_views": views,
            "mobile_selection": {
                "selected_view_ranks": [1, 2],
                "limitation_disposition": "included",
            },
            "data_limitations": ["A complete risk sentence " + "with detail " * 12 + "."],
        },
    }

    mobile = render_mobile_brief(analysis)

    assert "*1. Complete view 1*" in mobile
    assert "*2. Complete view 2*" in mobile
    assert "Why it moved:" in mobile
    assert "Options:" in mobile
    assert "Material conflict: The conflicting signal remains material." in mobile
    assert len(mobile) <= MAX_MOBILE_BRIEF_CHARS
    assert not mobile.endswith("…")

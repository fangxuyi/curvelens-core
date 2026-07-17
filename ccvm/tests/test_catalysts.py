"""Tests for catalyst extraction, ranking, and store — no live API calls."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ccvm.agents.catalyst_extractor import extract, _stable_event_id
from ccvm.agents.catalyst_ranker import score, rank_events
from ccvm.agents.catalyst_store import CatalystStore

AS_OF = date(2026, 6, 25)
FRONT_MONTH = "2026-08"

_MOCK_API_RESPONSE = {
    "content": [{
        "text": json.dumps({
            "event_type": "outage",
            "title": "Libya outage reduces Sahara blend export by 300kb/d",
            "effective_start": "2026-06-20",
            "effective_end": None,
            "commodity": "crude_oil",
            "region": "North Africa",
            "direction": "bullish_supply",
            "magnitude": "high",
            "affected_horizon": "prompt_3m",
            "source_quality": "high_quality_secondary",
            "evidence": ["Libya's Sharara field shuttered by protests"],
        })
    }]
}

_OPEC_EVENT = {
    "event_id": "opec01",
    "event_type": "opec",
    "title": "OPEC+ extends 1mb/d cut through Q3",
    "effective_start": "2026-07-01",
    "effective_end": "2026-09-30",
    "commodity": "crude_oil",
    "region": "Global",
    "direction": "bullish_supply",
    "magnitude": "high",
    "affected_horizon": "prompt_3m",
    "source_quality": "primary",
    "evidence": [],
    "published_at": "2026-06-25",
}

_DEMAND_EVENT = {
    "event_id": "demand01",
    "event_type": "macro_demand",
    "title": "China PMI falls to 48.2, demand outlook weakens",
    "effective_start": "2026-06-25",
    "effective_end": None,
    "commodity": "crude_oil",
    "region": "Asia",
    "direction": "bearish_demand",
    "magnitude": "medium",
    "affected_horizon": "6m",
    "source_quality": "high_quality_secondary",
    "evidence": [],
    "published_at": "2026-06-25",
}

_LOW_RELEVANCE_EVENT = {
    "event_id": "old01",
    "event_type": "other",
    "title": "Historical note: 2020 oil price war",
    "effective_start": "2020-03-01",
    "effective_end": "2020-05-01",
    "direction": "bearish_demand",
    "magnitude": "high",
    "affected_horizon": "structural",
    "source_quality": "other",
    "evidence": [],
}


# ──────────────────────────────────────────────────────────
# event_id stability
# ──────────────────────────────────────────────────────────

def test_event_id_stable():
    id1 = _stable_event_id("title", "2026-06-25", "outage")
    id2 = _stable_event_id("title", "2026-06-25", "outage")
    assert id1 == id2


def test_event_id_differs_for_different_inputs():
    a = _stable_event_id("title A", "2026-06-25", "outage")
    b = _stable_event_id("title B", "2026-06-25", "outage")
    assert a != b


# ──────────────────────────────────────────────────────────
# extraction (mocked API)
# ──────────────────────────────────────────────────────────

def test_extract_returns_event_with_mocked_api():
    mock_result = MagicMock(
        returncode=0,
        stdout=_MOCK_API_RESPONSE["content"][0]["text"],
        stderr="",
    )

    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("subprocess.run", return_value=mock_result),
    ):
        event = extract(
            text="Libya Sharara field shut down by protests.",
            source_url="https://example.com/libya",
            published_at="2026-06-25",
            observation_date=AS_OF,
            api_key="sk-test",
        )

    assert event is not None
    assert event["event_type"] == "outage"
    assert event["direction"] == "bullish_supply"
    assert "event_id" in event
    assert "observation_date" in event


def test_extract_returns_none_without_api_key():
    with patch("shutil.which", return_value=None):
        result = extract(
            text="Some article.",
            source_url="https://example.com",
            published_at="2026-06-25",
            observation_date=AS_OF,
            api_key="",
        )
    assert result is None


def test_extract_handles_no_catalyst_response():
    no_catalyst_resp = {
        "content": [{
            "text": json.dumps({
                "event_type": "other",
                "title": "no_catalyst_found",
                "effective_start": None, "effective_end": None,
                "commodity": "crude_oil", "region": None,
                "direction": "unclear", "magnitude": "unknown",
                "affected_horizon": "prompt_1m",
                "source_quality": "other", "evidence": [],
            })
        }]
    }
    mock_result = MagicMock(
        returncode=0,
        stdout=no_catalyst_resp["content"][0]["text"],
        stderr="",
    )

    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("subprocess.run", return_value=mock_result),
    ):
        event = extract("Generic news.", "https://x.com", "2026-06-25", AS_OF, api_key="sk-test")
    assert event is None


# ──────────────────────────────────────────────────────────
# ranking
# ──────────────────────────────────────────────────────────

def test_score_fields_present():
    scored = score(_OPEC_EVENT, AS_OF, FRONT_MONTH)
    assert "relevance_score" in scored
    assert "relevance_breakdown" in scored
    assert scored["relevance_score"] > 0


def test_prompt_event_scores_higher_than_old_event():
    s_prompt = score(_OPEC_EVENT, AS_OF, FRONT_MONTH)
    s_old = score(_LOW_RELEVANCE_EVENT, AS_OF, FRONT_MONTH)
    assert s_prompt["relevance_score"] > s_old["relevance_score"]


def test_rank_events_sorted_descending():
    ranked = rank_events([_DEMAND_EVENT, _OPEC_EVENT, _LOW_RELEVANCE_EVENT], AS_OF, FRONT_MONTH)
    scores = [e["relevance_score"] for e in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_events_adds_rank():
    ranked = rank_events([_OPEC_EVENT, _DEMAND_EVENT], AS_OF, FRONT_MONTH)
    ranks = [e["relevance_rank"] for e in ranked]
    assert ranks == [1, 2]


def test_high_magnitude_primary_source_scores_high():
    scored = score(_OPEC_EVENT, AS_OF, FRONT_MONTH)
    breakdown = scored["relevance_breakdown"]
    assert breakdown["magnitude"] == 20  # high magnitude = max
    assert breakdown["source_quality"] == 15  # primary = max


# ──────────────────────────────────────────────────────────
# catalyst store
# ──────────────────────────────────────────────────────────

def test_store_saves_and_loads(tmp_path):
    store = CatalystStore(tmp_path)
    events = [_OPEC_EVENT, _DEMAND_EVENT]
    written = store.save(events, AS_OF)
    assert written == 2

    loaded = store.load(AS_OF)
    assert len(loaded) == 2
    titles = [e["title"] for e in loaded]
    assert _OPEC_EVENT["title"] in titles


def test_store_deduplicates(tmp_path):
    store = CatalystStore(tmp_path)
    store.save([_OPEC_EVENT], AS_OF)
    written2 = store.save([_OPEC_EVENT], AS_OF)  # same event_id
    assert written2 == 0
    loaded = store.load(AS_OF)
    assert len(loaded) == 1


def test_store_appends_new_events(tmp_path):
    store = CatalystStore(tmp_path)
    store.save([_OPEC_EVENT], AS_OF)
    store.save([_DEMAND_EVENT], AS_OF)
    loaded = store.load(AS_OF)
    assert len(loaded) == 2


def test_store_load_empty_date(tmp_path):
    store = CatalystStore(tmp_path)
    loaded = store.load(date(2020, 1, 1))
    assert loaded == []


def test_store_load_range(tmp_path):
    store = CatalystStore(tmp_path)
    store.save([_OPEC_EVENT], date(2026, 6, 24))
    store.save([_DEMAND_EVENT], date(2026, 6, 25))
    events = store.load_range(date(2026, 6, 24), date(2026, 6, 25))
    assert len(events) == 2

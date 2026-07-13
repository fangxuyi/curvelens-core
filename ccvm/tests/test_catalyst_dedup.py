"""Tests for catalyst dedup/decay/clustering (C5) + event scenarios (C6)."""
from __future__ import annotations

from datetime import date

from ccvm.agents.catalyst_dedup import apply_decay, cluster_themes, dedupe, top_directional
from ccvm.scenarios.scenario_engine import event_shocks_from_catalysts


def _ev(title, score=80, direction="bullish_supply", etype="outage",
        start="2026-07-09", magnitude="high"):
    return {"title": title, "relevance_score": score, "direction": direction,
            "event_type": etype, "effective_start": start, "magnitude": magnitude,
            "affected_horizon": "prompt_1m"}


_HORMUZ = [
    _ev("US strikes on Iran raise Strait of Hormuz oil flow disruption risk", 88),
    _ev("Renewed Iran conflict threatens Strait of Hormuz energy flows", 78),
    _ev("Tanker traffic through Strait of Hormuz essentially stops", 78),
    _ev("EIA reports weekly crude inventory draw of 4 million barrels", 75,
        direction="bullish_supply", etype="inventory_release"),
]


class TestDedupe:
    def test_near_duplicates_collapse(self):
        out = dedupe(_HORMUZ)
        titles = [e["title"] for e in out]
        # the two "Strait of Hormuz ... threatens/raises" stories share enough
        # tokens to collapse; the EIA story survives independently
        assert len(out) < len(_HORMUZ)
        assert any("EIA" in t for t in titles)
        # survivor is the highest-scored (input pre-sorted desc)
        assert out[0]["relevance_score"] == 88
        assert out[0].get("duplicate_count", 1) >= 2

    def test_distinct_stories_survive(self):
        out = dedupe([_ev("OPEC+ announces surprise production cut"),
                      _ev("Hurricane shuts Gulf of Mexico platforms")])
        assert len(out) == 2


class TestDecay:
    def test_past_events_fade(self):
        evs = [_ev("old story", score=100, start="2026-07-01")]
        out = apply_decay(evs, date(2026, 7, 11))  # 10 days past
        assert out[0]["decayed_score"] == 50           # 100 × (1 − 0.05×10)
        assert out[0]["decay_days"] == 10

    def test_floor_at_40pct(self):
        evs = [_ev("ancient story", score=100, start="2026-01-01")]
        out = apply_decay(evs, date(2026, 7, 11))
        assert out[0]["decayed_score"] == 40

    def test_future_events_undecayed_and_resorted(self):
        evs = [_ev("old big", score=90, start="2026-06-01"),
               _ev("fresh medium", score=60, start="2026-07-11")]
        out = apply_decay(evs, date(2026, 7, 11))
        assert out[0]["title"] == "fresh medium"       # 60 beats 90×0.4=36


class TestThemesAndDirectional:
    def test_cluster_counts_absorb_duplicates(self):
        out = apply_decay(dedupe(_HORMUZ), date(2026, 7, 9))
        themes = cluster_themes(out)
        outage = next(t for t in themes if t["event_type"] == "outage")
        assert outage["count"] >= 3     # duplicates counted via duplicate_count

    def test_top_directional(self):
        evs = apply_decay(dedupe(_HORMUZ), date(2026, 7, 9))
        top = top_directional(evs, "bullish_supply")
        assert top is not None and top["relevance_score"] == 88
        assert top_directional(evs, "bearish_demand") is None


class TestEventShocks:
    def test_bull_event_shock(self):
        evs = apply_decay(dedupe(_HORMUZ), date(2026, 7, 9))
        shocks = event_shocks_from_catalysts(evs)
        assert len(shocks) == 1
        s = shocks[0]
        assert s.name == "event_bull"
        assert s.curve_shift_usd == 6.0            # high magnitude
        assert s.curve_tilt == -0.25               # prompt horizon
        assert "Hormuz" in s.description

    def test_low_score_no_shock(self):
        evs = apply_decay([_ev("minor note", score=50)], date(2026, 7, 9))
        assert event_shocks_from_catalysts(evs) == []

    def test_bear_event_negative_shift(self):
        evs = apply_decay([_ev("China demand collapses on lockdowns", 85,
                               direction="bearish_demand", etype="macro")],
                          date(2026, 7, 9))
        shocks = event_shocks_from_catalysts(evs)
        assert len(shocks) == 1 and shocks[0].curve_shift_usd == -6.0

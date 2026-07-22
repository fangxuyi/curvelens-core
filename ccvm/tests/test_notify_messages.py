"""Regression coverage for the migrated rich notification formatters."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
sys.path.insert(0, str(AGENT_DIR))
import notify  # noqa: E402


def _sections() -> dict:
    return {
        "market_risk": {
            "futures": {
                "front_contract": "CLQ26", "front_settlement": 72.5,
                "front_return_1d": 0.012, "contango": False,
                "m1_m2_spread": 0.65, "curve_slope_per_month": -0.12,
                "days_to_expiry": 18,
            },
            "options": {
                "atm_iv": 0.31, "risk_reversal_25d": -0.045,
            },
        },
        "what_changed": {
            "diff": {"status": "available", "settle_change": 1.1, "atm_iv_change": 0.012},
            "streaks": {"agreement_state_streak_days": 3},
        },
        "term_structure": {
            "m1_m6": 2.4, "roll_yield_annualized": 0.08,
            "front_2nd_iv_spread": 0.015,
        },
        "history_context": {
            "status": "available", "front_settle_pctile": 92,
            "m1_m2_pctile": 88, "atm_iv_pctile": 91, "rr25_pctile": 9,
            "benchmark_name": "Brent", "benchmark_spread_pctile": 75,
            "settle_30d_low": 66.0, "settle_30d_high": 74.0,
        },
        "fundamentals": {
            "status": "available", "eia_period": "2026-07-10",
            "crude_draw_mbbl": 4_200, "cushing_draw_mbbl": -500,
            "supply_signal": "tightening", "scenario_trigger": "bull_confirmed",
        },
        "eia_seasonal": {"trigger": "bull_confirmed"},
        "monitor": {
            "scenarios": {
                "bull": {"status": "live", "since": "2026-07-14"},
                "base": {"status": "live", "since": "2026-07-01"},
            },
            "trigger_results": [{
                "scenario": "bull", "side": "confirm", "fired": True,
                "description": "Options and futures confirm upside risk",
            }],
        },
        "oi": {"expiries": [{
            "expiry": "2026-08-17", "put_call_oi_ratio": 1.2, "max_pain": 70,
            "call_walls": [{"strike": 80}], "put_walls": [{"strike": 65}],
        }]},
        "scenarios": [{
            "name": "bull", "shocked_settlements": [{
                "base_settlement": 72.5, "shocked_settlement": 76.0, "diff": 3.5,
            }],
        }],
        "scorecard": {"render_ready": True, "states": [{
            "state": "confirmed_upside_risk", "n": 14,
            "avg_fwd_3d": 0.018, "hit_rate_3d": 0.64,
        }]},
        "catalysts": {"top_events": [{
            "score": 88, "direction": "bullish_supply", "title": "Supply disruption",
        }]},
        "next_review": {"scheduled_events": [{
            "date": "2026-07-22", "time_et": "10:30", "name": "EIA weekly petroleum",
        }]},
    }


def test_wti_daily_brief_preserves_rich_original_features():
    text = notify._daily_brief_text(
        "2026-07-16", _sections(),
        {"state": "confirmed_upside_risk", "confidence": "high"},
    )
    assert "Routine settlement digest" in text
    assert "*Curve:* backwardation" in text
    assert "*Context:* settle 92nd %ile" in text
    assert "*EIA Weekly Petroleum Fundamentals:*" in text
    assert "*Scenario board:*" in text
    assert "*Options positioning:*" in text
    assert "*Next scheduled:* 2026-07-22 10:30 ET" in text


def test_wti_priority_alert_includes_triggers_levels_and_scorecard():
    text = notify._priority_alert_text(
        "2026-07-16", _sections(),
        {"state": "confirmed_upside_risk", "confidence": "high", "evidence": ["curve rose"]},
        "bull_confirmed",
    )
    assert "*Why it fired:*" in text
    assert "*Scenario triggers:*" in text
    assert "*Context / levels:*" in text
    assert "bull case front settle" in text
    assert "Prior confirmed upside risk scorecard" in text


def test_rich_formatting_does_not_reintroduce_wti_units(monkeypatch):
    gold = SimpleNamespace(
        currency="USD", price_unit="USD/OZT", benchmark=None,
        fundamentals_provider=None,
    )
    monkeypatch.setattr(notify, "_product", lambda: gold)
    sections = _sections()
    sections["market_risk"]["futures"]["front_contract"] = "GCQ26"
    sections["market_risk"]["futures"]["front_settlement"] = 2350.0
    sections["fundamentals"] = {}
    sections["eia_seasonal"] = {}
    text = notify._priority_alert_text(
        "2026-07-16", sections,
        {"state": "confirmed_upside_risk", "confidence": "high"}, "none",
    )
    assert "$2,350.00/ozt" in text
    assert "/bbl" not in text
    assert "Brent-WTI" not in text


def test_daily_delivery_requires_completed_agent_orchestration(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(notify, "DATA_DIR", tmp_path)
    with pytest.raises(SystemExit) as exc:
        notify.cmd_prepare("2026-07-20")
    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "ANALYSIS_NOT_COMPLETE"

    state_path = tmp_path / "analysis_workflow" / "trade_date=2026-07-20" / "run.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"phase": "SPECIALISTS_REQUIRED"}))
    with pytest.raises(SystemExit) as exc:
        notify.cmd_prepare("2026-07-20")
    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "SPECIALISTS_REQUIRED"


def test_agent_synthesis_message_preserves_numbers_sections_news_and_plain_language(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(notify, "DATA_DIR", tmp_path)
    analysis_dir = tmp_path / "analysis" / "trade_date=2026-07-20"
    analysis_dir.mkdir(parents=True)
    metric = lambda label, value, meaning: {
        "label": label, "value": value, "comparison": "prior day",
        "plain_english_meaning": meaning, "evidence_ids": ["feature:test"],
    }
    analysis = {
        "product": "gold",
        "synthesis": {
            "headline": "Gold was little changed while options favored downside protection",
            "plain_english_summary": "Gold slipped slightly. The options market paid more for downside protection.",
            "confirmations": ["Settlement falls below $4,015.90/oz."],
            "invalidations": ["Settlement recovers above $4,018.80/oz."],
            "data_limitations": ["Only two local GC settlement dates are available."],
            "top_views": [
                {"rank": 1, "title": "Curve carry is a headwind",
                 "plain_english_view": "Deferred gold settled above the front contract.",
                 "evidence_relationship": "cross_supported", "confidence": "medium",
                 "horizon": "next week", "key_metrics": [
                     metric("Front settlement", "$4,015.90/oz", "Gold fell $2.90 on the day."),
                     metric("M1-M3 spread", "-$27.30/oz", "The curve is in contango."),
                 ], "supporting_evidence": [{"claim": "Price and curve evidence are defensive."}],
                 "conflicting_evidence": []},
                {"rank": 2, "title": "Options favor downside protection",
                 "plain_english_view": "Puts carry more implied volatility than calls.",
                 "evidence_relationship": "single_desk", "confidence": "medium",
                 "horizon": "front expiry", "key_metrics": [
                     metric("Front ATM IV", "19.94%", "This is the annualized volatility estimate."),
                     metric("25-delta risk reversal", "-3.26 vol points", "Puts cost more volatility than calls."),
                     metric("25-delta butterfly", "+0.45 vol points", "The wings are richer than the center."),
                 ], "supporting_evidence": [{"claim": "Skew is negative."}],
                 "conflicting_evidence": []},
                {"rank": 3, "title": "Macro evidence is mixed",
                 "plain_english_view": "Lower real yields help gold, while the dollar offsets that support.",
                 "evidence_relationship": "conflicting", "confidence": "low",
                 "horizon": "next week", "key_metrics": [
                     metric("10-year real yield", "2.31%", "It fell 4 basis points."),
                     metric("Broad dollar", "120.53", "It rose 0.17%."),
                 ], "supporting_evidence": [{"claim": "Real yields fell."}],
                 "conflicting_evidence": [{"claim": "The dollar strengthened."}]},
            ],
        },
        "specialist_analyses": {
            "futures_curve": {"key_metrics": [
                metric("Front settlement", "$4,015.90/oz", "Gold fell $2.90 on the day."),
                metric("M1-M3 spread", "-$27.30/oz", "The curve is in contango."),
            ], "news_findings": []},
            "vol_surface": {"key_metrics": [
                metric("Front ATM IV", "19.94%", "This is the market's annualized volatility estimate."),
                metric("25-delta risk reversal", "-3.26 vol points", "Puts cost more volatility than calls."),
                metric("25-delta butterfly", "+0.45 vol points", "The wings are richer than the center."),
            ], "news_findings": []},
            "macro": {"key_metrics": [
                metric("10-year real yield", "2.31%", "It fell 4 basis points, which can support gold."),
            ], "news_findings": [{"claim": "No relevant dated macro catalyst was collected."}]},
        },
    }
    (analysis_dir / "analysis.json").write_text(json.dumps(analysis))
    text = notify._analysis_synthesis_text("2026-07-20")
    assert "*1. Curve carry is a headwind*" in text
    assert "$4,015.90/oz" in text and "-$27.30/oz" in text
    assert "*2. Options favor downside protection*" in text
    assert "19.94%" in text and "-3.26 vol points" in text and "+0.45 vol points" in text
    assert "*3. Macro evidence is mixed*" in text and "2.31%" in text
    assert "Conflicts: The dollar strengthened." in text
    assert "*Data notes*" in text
    assert len(text) <= 3900

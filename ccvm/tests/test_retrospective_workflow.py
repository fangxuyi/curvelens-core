from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from ccvm.workflow.finalize import AnalysisValidationError
from ccvm.workflow.retrospective import (
    prepare_retrospective,
    refresh_retrospectives,
    validate_retrospective_response,
)


def _contract():
    return {
        "version": 2,
        "dimensions": {"price_direction": {
            "metric_key": "front_settlement_return",
            "labels": ["down", "flat", "up"],
            "outcome_rule": {
                "source_metric": "front_settlement", "calculation": "return",
                "kind": "signed_band", "thresholds": [0.0025],
                "labels": ["down", "flat", "up"],
            },
        }},
    }


def _mobile_contract():
    return {
        "version": 1, "horizon_sessions": 1,
        "dimensions": {"price_direction": {"thresholds": [0.005, 0.015]}},
    }


def _investigator_contract():
    return {
        "version": 1, "horizons_sessions": [1, 5],
        "dimensions": {"price_direction": {
            **_contract()["dimensions"]["price_direction"],
            "thresholds_by_horizon": {"1": [0.005, 0.015], "5": [0.01, 0.03]},
        }},
    }


def _mobile_selection():
    return {
        "selected_view_ranks": [1],
        "selection_rationale": "The first view has the highest expected impact.",
        "candidates": [{
            "source_view_rank": rank,
            "disposition": "selected" if rank == 1 else "omitted",
            "materiality": "high" if rank == 1 else "low",
            "expected_impact_dimensions": ["price_direction"],
            "rationale": "Current evidence determined the ex-ante priority.",
            "evidence_ids": ["feature:market:2026-07-02"],
        } for rank in (1, 2, 3)],
        "limitation_disposition": "not_applicable",
        "limitation_rationale": "No material limitation was reported.",
    }


def _setup(root: Path, *, target=True, with_investigator=False):
    trade_date = "2026-07-02"
    analysis_dir = root / "analysis" / f"trade_date={trade_date}"
    analysis_dir.mkdir(parents=True)
    forecast = {
        "forecast_id": "packet:v1:price_direction:h1",
        "source_view_rank": 1, "dimension": "price_direction",
        "metric_key": "front_settlement_return", "horizon_sessions": 1,
        "expected_label": "up", "confidence": "high",
        "evidence_ids": ["feature:market:2026-07-02"],
    }
    analysis = {
        "product": "gold", "trade_date": trade_date,
        "forecast_contract": _contract(),
        "mobile_relevance_contract": _mobile_contract(),
        "synthesis": {
            "forecast_ledger": [forecast], "top_views": [{"rank": 1}],
            "mobile_selection": _mobile_selection(),
        },
    }
    if with_investigator:
        finding_id = "packet1234567890:futures_curve:f1"
        investigation_id = "packet1234567890:futures_curve"
        analysis.update({
            "investigator_relevance_contract": _investigator_contract(),
            "investigator_analyses": {"futures_curve": {
                "investigation_id": investigation_id,
                "candidate_findings": [{
                    "finding_id": finding_id, "materiality": "medium",
                    "horizon_sessions": 1, "confidence": "medium",
                    "expected_impact_dimensions": ["price_direction"],
                    "evidence_ids": ["feature:market:2026-07-02"],
                }],
            }},
        })
        analysis["synthesis"]["investigator_feedback"] = [{
            "investigation_id": investigation_id, "disposition": "rejected",
            "used_finding_ids": [],
        }]
    (analysis_dir / "analysis.json").write_text(json.dumps(analysis))
    reports = root / "reports"
    reports.mkdir()
    for dt, settle in [(trade_date, 100), ("2026-07-06", 101)]:
        if dt == "2026-07-06" and not target:
            continue
        (reports / f"{dt}.json").write_text(json.dumps({
            "trade_date": dt, "sections": {"market_risk": {
                "futures": {"front_settlement": settle},
                "options": {"atm_iv": 0.2},
            }},
        }))
    workflow = root / "analysis_workflow" / f"trade_date={trade_date}"
    workflow.mkdir(parents=True)
    (workflow / "workflow_events.jsonl").write_text(
        json.dumps({"event": "validation_rejected", "actor": "synthesis"}) + "\n"
        + json.dumps({"event": "run_finalized", "actor": "controller"}) + "\n"
    )
    return trade_date


def test_retrospective_materializes_outcomes_evaluation_and_action(tmp_path):
    trade_date = _setup(tmp_path)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    assert result["result"] == "RETROSPECTIVE_REQUIRED"
    assert result["actions"][0]["agent_type"] == "curvelens_retrospective"
    packet = json.loads(Path(result["packet"]).read_text())
    assert packet["evaluations"][0]["hit"] is True
    assert packet["trace_summary"]["validation_rejections"] == 1
    assert packet["mobile_relevance"]["records"][0]["selection_correct"] is True
    assert packet["mobile_relevance"]["aggregate"]["precision"] == 1
    assert "hidden chain-of-thought" in packet["trace_summary"]["scope_note"]
    assert packet["review_contract"]["candidate_rule"].startswith(
        "Leave candidate_advisories empty"
    )
    assert "leave candidate_advisories as an empty list" in Path(
        result["actions"][0]["task_path"]
    ).read_text()


def test_invalid_retrospective_is_archived_and_redispatched(tmp_path):
    trade_date = _setup(tmp_path)
    now = datetime.fromisoformat("2026-07-07T12:00:00+00:00")
    first = prepare_retrospective(tmp_path, trade_date, generated_at=now)
    action = first["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
        "mobile_assessment": "Done.", "investigator_assessment": "Done.",
        "candidate_advisories": [{"candidate_id": "legacy-key"}],
    })
    response["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "appropriate", "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))

    retry = prepare_retrospective(tmp_path, trade_date, generated_at=now)

    assert retry["result"] == "RETROSPECTIVE_REQUIRED"
    assert "candidate_advisories[0]" in retry["validation_error"]
    assert Path(retry["invalid_response"]).exists()
    assert not Path(action["response_path"]).exists()
    assert retry["actions"][0]["response_path"] == action["response_path"]
    assert "prior response failed validation" in Path(action["task_path"]).read_text()


def test_retrospective_scores_rejected_investigator_finding_materiality(tmp_path):
    trade_date = _setup(tmp_path, with_investigator=True)
    result = prepare_retrospective(
        tmp_path, trade_date,
        generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    packet = json.loads(Path(result["packet"]).read_text())
    record = packet["investigator_relevance"]["records"][0]
    assert record["status"] == "scored"
    assert record["realized_materiality"] == "material"
    assert record["materiality_hit"] is True
    assert record["rejected_but_material"] is True
    template = json.loads(Path(result["actions"][0]["template_path"]).read_text())
    assert template["investigator_reviews"][0]["finding_id"] == record["finding_id"]


def test_valid_response_completes_without_activating_candidates(tmp_path):
    trade_date = _setup(tmp_path)
    now = datetime.fromisoformat("2026-07-07T12:00:00+00:00")
    first = prepare_retrospective(tmp_path, trade_date, generated_at=now)
    action = first["actions"][0]
    template = json.loads(Path(action["template_path"]).read_text())
    template.update({
        "status": "complete", "outcome_assessment": "The forecast matched the label.",
        "trace_assessment": "One validation retry was visible.",
        "priority_assessment": "The first-ranked view received full weight.",
        "mobile_assessment": "The selected view was materially relevant.",
        "investigator_assessment": "No investigator findings were available.",
    })
    template["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "The stated direction matched.",
        "improvement": "Retain the explicit threshold and evidence link.",
    })
    template["mobile_reviews"][0].update({
        "assessment": "appropriate",
        "diagnosis": "The selected view was associated with a material move.",
        "improvement": "Retain the ex-ante materiality threshold.",
    })
    Path(action["response_path"]).write_text(json.dumps(template))
    result = prepare_retrospective(tmp_path, trade_date, generated_at=now)
    assert result["result"] == "RETROSPECTIVE_COMPLETE"
    final = json.loads(Path(result["retrospective"]).read_text())
    assert final["review"]["candidate_advisories"] == []


def test_response_cannot_override_deterministic_score(tmp_path):
    trade_date = _setup(tmp_path)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    action = result["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
        "mobile_assessment": "Done.",
        "investigator_assessment": "Done.",
    })
    response["forecast_reviews"][0].update({
        "assessment": "incorrect", "diagnosis": "No.", "improvement": "No.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "appropriate", "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))
    with pytest.raises(AnalysisValidationError, match="preserve deterministic scoring"):
        validate_retrospective_response(
            Path(action["packet_path"]), Path(action["response_path"]),
        )


def test_response_cannot_override_mobile_relevance_score(tmp_path):
    trade_date = _setup(tmp_path)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    action = result["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
        "mobile_assessment": "Done.",
        "investigator_assessment": "Done.",
    })
    response["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "false_prominence", "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))

    with pytest.raises(AnalysisValidationError, match="preserve deterministic relevance scoring"):
        validate_retrospective_response(
            Path(action["packet_path"]), Path(action["response_path"]),
        )


def test_response_cannot_override_investigator_attribution(tmp_path):
    trade_date = _setup(tmp_path, with_investigator=True)
    result = prepare_retrospective(
        tmp_path, trade_date,
        generated_at=datetime.fromisoformat("2026-07-07T12:00:00+00:00"),
    )
    action = result["actions"][0]
    response = json.loads(Path(action["template_path"]).read_text())
    response.update({
        "status": "complete", "outcome_assessment": "Done.",
        "trace_assessment": "Done.", "priority_assessment": "Done.",
        "mobile_assessment": "Done.", "investigator_assessment": "Done.",
    })
    response["forecast_reviews"][0].update({
        "assessment": "correct", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["mobile_reviews"][0].update({
        "assessment": "appropriate", "diagnosis": "Done.", "improvement": "Done.",
    })
    response["investigator_reviews"][0].update({
        "assessment": "overstated", "lead_disposition": "used",
        "diagnosis": "Done.", "improvement": "Done.",
    })
    Path(action["response_path"]).write_text(json.dumps(response))
    with pytest.raises(AnalysisValidationError, match="deterministic attribution"):
        validate_retrospective_response(
            Path(action["packet_path"]), Path(action["response_path"]),
        )


def test_retrospective_is_pending_until_target_session_exists(tmp_path):
    trade_date = _setup(tmp_path, target=False)
    result = prepare_retrospective(
        tmp_path, trade_date, generated_at=datetime.fromisoformat("2026-07-03T12:00:00+00:00"),
    )
    assert result["result"] == "RETROSPECTIVE_PENDING"
    assert result["actions"] == []


def test_refresh_finds_historical_reviews_without_blocking_on_legacy_files(tmp_path):
    _setup(tmp_path)
    legacy = tmp_path / "analysis" / "trade_date=2026-07-01"
    legacy.mkdir(parents=True)
    (legacy / "analysis.json").write_text(json.dumps({
        "product": "gold", "trade_date": "2026-07-01",
    }))
    result = refresh_retrospectives(tmp_path, date.fromisoformat("2026-07-07"))
    assert result["actions"][0]["action"] == "RUN_RETROSPECTIVE"
    assert result["errors"][0]["trade_date"] == "2026-07-01"


def _finish_review(result):
    """Supply a valid reviewer response to exercise cache/controller behavior."""
    packet = json.loads(Path(result['packet']).read_text())
    action = result['actions'][0]
    response = json.loads(Path(action['template_path']).read_text())
    response['status'] = 'complete'
    for key in ('outcome_assessment', 'trace_assessment', 'priority_assessment',
                'mobile_assessment', 'investigator_assessment'):
        response[key] = 'Reviewed the supplied evidence.'
    forecasts = {item['forecast_id']: item for item in packet['evaluations']}
    mobile = {item['source_view_rank']: item for item in packet['mobile_relevance']['records']}
    investigators = {item['finding_id']: item for item in packet['investigator_relevance']['records']}
    for item in response['forecast_reviews']:
        item.update(assessment='correct' if forecasts[item['forecast_id']]['hit'] else 'incorrect',
                    diagnosis='Compared the fixed labels.', improvement='Retain explicit horizons.')
    for item in response['mobile_reviews']:
        score = mobile[item['source_view_rank']]
        label = 'missed_material' if score['missed_material'] else (
            'false_prominence' if score['false_prominence'] else 'appropriate')
        item.update(assessment=label, diagnosis='Compared relevance.', improvement='Retain evidence.')
    for item in response['investigator_reviews']:
        score = investigators[item['finding_id']]
        expected = {'low': 0, 'medium': 1, 'high': 2}[score['expected_materiality']]
        actual = score['materiality_score']
        label = 'materiality_matched' if actual == expected else (
            'overstated' if expected > actual else 'understated')
        item.update(assessment=label, diagnosis='Compared materiality.', improvement='Retain attribution.')
    Path(action['response_path']).write_text(json.dumps(response))


def _legacy_review(root, trade_date):
    """Emulate the pre-fix cache, including its raw, absolute-path log hash."""
    import hashlib
    result = prepare_retrospective(root, trade_date)
    _finish_review(result)
    completed = prepare_retrospective(root, trade_date)
    packet_path = Path(completed['packet'])
    packet = json.loads(packet_path.read_text())
    identity = {key: value for key, value in packet['cache_identity'].items()
                if key not in {'cache_identity_version', 'review_scores_sha256'}}
    identity['events_sha256'] = packet['source_artifacts']['workflow_events_sha256']
    legacy_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    packet['packet_id'] = legacy_id
    packet.pop('cache_identity')
    packet_path.write_text(json.dumps(packet))
    final_path = Path(completed['retrospective'])
    final = json.loads(final_path.read_text())
    final['packet_id'] = final['review']['packet_id'] = legacy_id
    final.pop('cache_identity')
    final_path.write_text(json.dumps(final))
    (final_path.parent / 'retrospective.response.json').write_text(json.dumps(final['review']))
    return legacy_id


def _add_event_path(root, trade_date):
    events = root / 'analysis_workflow' / f'trade_date={trade_date}' / 'workflow_events.jsonl'
    with events.open('a') as handle:
        handle.write(json.dumps({'event': 'agent_dispatched', 'actor': 'synthesis',
                                 'task_path': str(root / 'analysis_workflow' / 'task.md')}) + '\n')
    return events


def test_cache_survives_relocation_and_clock_changes(tmp_path):
    import shutil
    original = tmp_path / 'original'
    moved = tmp_path / 'moved'
    dt = _setup(original, with_investigator=True)
    _add_event_path(original, dt)
    first = prepare_retrospective(original, dt)
    _finish_review(first)
    prepare_retrospective(original, dt)
    shutil.copytree(original, moved)
    events = moved / 'analysis_workflow' / f'trade_date={dt}' / 'workflow_events.jsonl'
    events.write_text(events.read_text().replace(str(original), str(moved)))
    result = prepare_retrospective(moved, dt, generated_at=datetime.fromisoformat('2026-10-01T00:00:00+00:00'))
    assert result['result'] == 'RETROSPECTIVE_COMPLETE'
    assert result['actions'] == []
    assert json.loads(Path(result['packet']).read_text())['packet_id'] == json.loads(Path(first['packet']).read_text())['packet_id']


@pytest.mark.parametrize('change', ['outcome', 'analysis', 'event', 'scoring_version', 'investigator_outcome'])
def test_cache_invalidates_for_real_evidence_changes(tmp_path, monkeypatch, change):
    import ccvm.workflow.retrospective as retrospective
    dt = _setup(tmp_path, with_investigator=True)
    if change == 'investigator_outcome':
        path = tmp_path / 'analysis' / f'trade_date={dt}' / 'analysis.json'
        analysis = json.loads(path.read_text())
        analysis['investigator_analyses']['futures_curve']['candidate_findings'][0]['horizon_sessions'] = 5
        path.write_text(json.dumps(analysis))
    first = prepare_retrospective(tmp_path, dt)
    _finish_review(first)
    prepare_retrospective(tmp_path, dt)
    if change == 'outcome':
        path = tmp_path / 'reports' / '2026-07-06.json'
        report = json.loads(path.read_text())
        report['sections']['market_risk']['futures']['front_settlement'] = 90
        path.write_text(json.dumps(report))
    elif change == 'investigator_outcome':
        report = json.loads((tmp_path / 'reports' / '2026-07-06.json').read_text())
        report['trade_date'] = '2026-07-10'
        (tmp_path / 'reports' / '2026-07-10.json').write_text(json.dumps(report))
    elif change == 'analysis':
        path = tmp_path / 'analysis' / f'trade_date={dt}' / 'analysis.json'
        analysis = json.loads(path.read_text())
        analysis['synthesis']['top_views'][0]['explanation'] = 'Corrected evidence.'
        path.write_text(json.dumps(analysis))
    elif change == 'event':
        events = tmp_path / 'analysis_workflow' / f'trade_date={dt}' / 'workflow_events.jsonl'
        with events.open('a') as handle:
            handle.write(json.dumps({'event': 'validation_rejected', 'actor': 'synthesis'}) + '\n')
    else:
        monkeypatch.setattr(retrospective, 'EVALUATOR_VERSION', 2)
    assert prepare_retrospective(tmp_path, dt)['result'] == 'RETROSPECTIVE_REQUIRED'


@pytest.mark.parametrize('relocated', [False, True])
def test_legacy_review_recovery_requires_exact_proof(tmp_path, relocated):
    import shutil
    original = tmp_path / 'original'
    dt = _setup(original, with_investigator=True)
    _add_event_path(original, dt)
    legacy_id = _legacy_review(original, dt)
    root = original
    if relocated:
        root = tmp_path / 'moved'
        shutil.copytree(original, root)
        events = root / 'analysis_workflow' / f'trade_date={dt}' / 'workflow_events.jsonl'
        events.write_text(events.read_text().replace(str(original), str(root)))
        # Simulate the overnight run replacing the packet and deleting the old response.
        assert prepare_retrospective(root, dt)['result'] == 'RETROSPECTIVE_REQUIRED'
    result = prepare_retrospective(root, dt, previous_data_root=original if relocated else None)
    assert result['result'] == 'RETROSPECTIVE_COMPLETE'
    assert list(Path(result['retrospective']).parent.glob(f'retrospective.reused-{legacy_id}.json'))
    assert prepare_retrospective(root, dt)['result'] == 'RETROSPECTIVE_COMPLETE'


def test_relocation_proof_does_not_reuse_changed_evidence(tmp_path):
    import shutil
    original = tmp_path / 'original'
    dt = _setup(original)
    _add_event_path(original, dt)
    _legacy_review(original, dt)
    moved = tmp_path / 'moved'
    shutil.copytree(original, moved)
    events = moved / 'analysis_workflow' / f'trade_date={dt}' / 'workflow_events.jsonl'
    events.write_text(events.read_text().replace(str(original), str(moved)) +
                      json.dumps({'event': 'validation_rejected', 'actor': 'synthesis'}) + '\n')
    assert prepare_retrospective(moved, dt, previous_data_root=original)['result'] == 'RETROSPECTIVE_REQUIRED'


def test_daily_review_budget_persists_across_repeated_learn_calls(tmp_path, monkeypatch):
    import ccvm.workflow.retrospective as retrospective
    dates = ['2026-07-01', '2026-07-02', '2026-07-06', '2026-07-07']
    for dt in dates:
        path = tmp_path / 'analysis' / f'trade_date={dt}' / 'analysis.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}')
    done = set()
    def prepare(root, dt):
        return {'result': 'RETROSPECTIVE_COMPLETE' if dt in done else 'RETROSPECTIVE_REQUIRED',
                'actions': [] if dt in done else [{'date': dt}]}
    monkeypatch.setattr(retrospective, 'prepare_retrospective', prepare)
    first = refresh_retrospectives(tmp_path, date(2026, 7, 8))
    assert first['actions'] == [{'date': '2026-07-07'}, {'date': '2026-07-06'}]
    assert first['deferred_source_dates'] == ['2026-07-02', '2026-07-01']
    assert refresh_retrospectives(tmp_path, date(2026, 7, 8))['actions'] == first['actions']
    done.update(['2026-07-07', '2026-07-06'])
    second = refresh_retrospectives(tmp_path, date(2026, 7, 8))
    assert second['actions'] == []
    assert second['deferred_source_dates'] == first['deferred_source_dates']
    assert refresh_retrospectives(tmp_path, date(2026, 7, 9))['actions'] == [
        {'date': '2026-07-02'}, {'date': '2026-07-01'}]


def test_malformed_response_still_uses_bounded_correction(tmp_path):
    dt = _setup(tmp_path)
    first = prepare_retrospective(tmp_path, dt)
    Path(first['actions'][0]['response_path']).write_text('{')
    retry = prepare_retrospective(tmp_path, dt)
    assert retry['result'] == 'RETROSPECTIVE_REQUIRED'
    assert 'not valid JSON' in retry['validation_error']
    assert Path(retry['invalid_response']).exists()


def test_cache_accepts_symlink_spelling_of_data_root(tmp_path):
    real = tmp_path / 'real'
    link = tmp_path / 'link'
    dt = _setup(real)
    link.symlink_to(real, target_is_directory=True)
    events = _add_event_path(link, dt)
    first = prepare_retrospective(link, dt)
    _finish_review(first)
    prepare_retrospective(link, dt)
    assert prepare_retrospective(real, dt)['result'] == 'RETROSPECTIVE_COMPLETE'
    events.write_text(events.read_text().replace(str(link), str(real)))
    assert prepare_retrospective(real, dt)['result'] == 'RETROSPECTIVE_COMPLETE'

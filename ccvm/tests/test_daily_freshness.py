"""A completed historical run must not satisfy a newer invocation target."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ccvm.reference.product import load_product
from ccvm.workflow.orchestration import initialize_state, save_state
from ccvm.workflow.packets import build_analysis_packets

REPO = Path(__file__).resolve().parents[2]
OLD, TARGET = "2026-09-16", "2026-09-17"
QUALITY = {"overall_status": "PASS", "futures": {"status": "PASS"},
           "options": {"status": "PASS"}}


def _module(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "agent" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(root, trade_date, product="wti"):
    directory = root / "analysis_workflow" / f"trade_date={trade_date}"
    build_analysis_packets(
        product=load_product(product), trade_date=trade_date,
        report={"sections": {}}, quality=QUALITY, articles=[], output_dir=directory,
    )
    return {"result": "ANALYSIS_PACKETS_READY", "manifest": str(directory / "manifest.json"),
            "quality_report": QUALITY, "quality_attempts": []}


def _completed(root, product="wti"):
    prepared = _prepared(root, OLD, product)
    path, state = initialize_state(
        manifest_path=Path(prepared["manifest"]), quality=QUALITY,
        quality_attempts=[], repo_root=REPO,
    )
    # This fixture exercises resumption of already finalized state, not synthesis.
    state["phase"] = "COMPLETE"
    report_dir = root / "analysis" / f"trade_date={OLD}"
    report_dir.mkdir(parents=True)
    for field, filename in (("analysis_json", "analysis.json"), ("analysis_md", "analysis.md"),
                            ("statistics_md", "statistics.md"), ("mobile_md", "mobile.md")):
        output = report_dir / filename
        output.write_text(json.dumps({key: state[key] for key in
                                     ("product", "trade_date", "packet_id")})
                          if filename.endswith("json") else "Historical report")
        state[field] = str(output)
    save_state(path, state)
    bulletin = root / "cme_bulletin"
    bulletin.mkdir()
    (bulletin / f"{OLD}.pdf").write_bytes(b"PRELIMINARY fixture")
    (bulletin / f"{OLD}.final-vintage.pdf").write_bytes(b"FINAL fixture")
    budget = root / "learning/review_dispatch/2026-09-18.json"
    budget.parent.mkdir(parents=True)
    budget.write_text(json.dumps({"as_of": "2026-09-18", "limit": 2,
                                  "source_dates": ["2026-09-15", "2026-09-14"]}))
    return state


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _invoke(module, monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", [module.__file__, *args])
    with pytest.raises(SystemExit) as exc:
        module.main()
    captured = capsys.readouterr()
    return exc.value.code, json.loads(captured.out) if captured.out else None


@pytest.mark.parametrize("product", ["wti", "gold", "corn", "silver"])
def test_stale_completed_date_is_blocked_before_resume_or_restart(tmp_path, product):
    _completed(tmp_path, product)
    before = _snapshot(tmp_path)
    env = {**os.environ, "CCVM_PRODUCT": product, "CCVM_DATA_DIR": str(tmp_path)}
    # Exercise real CLI wiring and the production-like September 16/17 mismatch.
    for command in ("start", "advance", "status", "inspect"):
        args = [sys.executable, str(REPO / "agent/analysis_orchestrator.py"), command,
                "--date", OLD, "--expected-date", TARGET]
        if command == "start":
            args.append("--restart")  # Even this must not destroy the historical state.
        proc = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
        result = json.loads(proc.stdout)
        assert proc.returncode == 1
        assert result["result"] == "AWAITING_TRADE_DATE"
        assert result["date"] == OLD and result["expected_trade_date"] == TARGET
        assert result["actions"] == [] and result["delivery_queued"] is False
        assert _snapshot(tmp_path) == before


def test_new_date_starts_with_completed_history_and_exhausted_learning_budget(
    tmp_path, monkeypatch, capsys,
):
    _completed(tmp_path)
    before = _snapshot(tmp_path)
    controller = _module("analysis_orchestrator")
    monkeypatch.setattr(controller, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "get_product", lambda: load_product("wti"))
    prepared_dates = []

    def prepare(value):
        prepared_dates.append(value)
        return _prepared(tmp_path, value)

    def no_learning(*args, **kwargs):
        pytest.fail("Daily start/resumption must not call historical learning")

    monkeypatch.setattr(controller, "_prepare", prepare)
    monkeypatch.setattr(controller, "refresh_retrospectives", no_learning)
    monkeypatch.setattr(controller, "build_memory", no_learning)
    code, result = _invoke(controller, monkeypatch, capsys, "start", "--date", TARGET,
                           "--expected-date", TARGET)
    assert code == 0 and result["result"] == "ORCHESTRATION_ACTIVE"
    assert result["date"] == result["expected_trade_date"] == TARGET
    assert result["actions"][0]["action"] == "RUN_QC_REVIEWER"
    assert prepared_dates == [TARGET]
    assert all(_snapshot(tmp_path)[path] == content for path, content in before.items())
    # Same-target retries reuse the new workflow rather than reprepare evidence.
    _, resumed = _invoke(controller, monkeypatch, capsys, "start", "--date", TARGET,
                          "--expected-date", TARGET)
    assert resumed["run_id"] == result["run_id"]
    assert prepared_dates == [TARGET]


@pytest.mark.parametrize("expected", [None, OLD])
def test_explicit_historical_resume_still_reuses_completed_report(
    tmp_path, monkeypatch, capsys, expected,
):
    saved = _completed(tmp_path)
    controller = _module("analysis_orchestrator")
    monkeypatch.setattr(controller, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "_prepare", lambda _: pytest.fail("must reuse history"))
    args = ["start", "--date", OLD]
    if expected:
        args.extend(["--expected-date", expected])
    code, result = _invoke(controller, monkeypatch, capsys, *args)
    assert code == 0 and result["result"] == "ORCHESTRATION_COMPLETE"
    assert result["run_id"] == saved["run_id"] and result["actions"] == []
    assert result["analysis_md"] == saved["analysis_md"]


@pytest.mark.parametrize("actual,expected,status", [
    ("2026-09-18", TARGET, "UNEXPECTED_TRADE_DATE"),
    (OLD, "invalid", "ERROR"),
])
def test_bad_target_or_future_source_fails_without_creating_runtime_state(
    tmp_path, monkeypatch, capsys, actual, expected, status,
):
    controller = _module("analysis_orchestrator")
    monkeypatch.setattr(controller, "data_dir", lambda: tmp_path)
    code, result = _invoke(controller, monkeypatch, capsys, "start", "--date", actual,
                           "--expected-date", expected)
    assert code == 1 and result["result"] == status
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("args", [
    ["start", "--expected-date", TARGET],
    ["learn", "--date", "2026-09-18", "--expected-date", TARGET],
])
def test_target_requires_explicit_source_date_and_cannot_change_learning_cutoff(
    tmp_path, monkeypatch, capsys, args,
):
    controller = _module("analysis_orchestrator")
    monkeypatch.setattr(controller, "data_dir", lambda: tmp_path)
    code, _ = _invoke(controller, monkeypatch, capsys, *args)
    assert code == 2 and not list(tmp_path.iterdir())


@pytest.mark.parametrize("expected,result_name", [
    (TARGET, "AWAITING_TRADE_DATE"), ("2026-09-15", "UNEXPECTED_TRADE_DATE"),
    ("invalid", "ERROR"),
])
def test_delivery_target_guard_preserves_outbox(tmp_path, monkeypatch, capsys, expected, result_name):
    _completed(tmp_path)
    notify = _module("notify")
    monkeypatch.setattr(notify, "DATA_DIR", tmp_path)
    pending = tmp_path / "agent_outbox/pending.json"
    pending.parent.mkdir()
    pending.write_text('[{"id": "existing-item"}]')
    monkeypatch.setattr(notify, "PENDING_PATH", pending)
    monkeypatch.setattr(notify, "DELIVERED_PATH", pending.parent / "delivered.json")
    before = _snapshot(tmp_path)
    code, result = _invoke(notify, monkeypatch, capsys, "--prepare", "--date", OLD,
                           "--expected-date", expected)
    assert code == 1 and result["result"] == result_name
    assert _snapshot(tmp_path) == before


def test_matching_delivery_does_not_wait_for_learning_and_keeps_deduplication(
    tmp_path, monkeypatch, capsys,
):
    _completed(tmp_path)
    notify = _module("notify")
    monkeypatch.setattr(notify, "DATA_DIR", tmp_path)
    monkeypatch.setattr(notify, "PENDING_PATH", tmp_path / "agent_outbox/pending.json")
    monkeypatch.setattr(notify, "DELIVERED_PATH", tmp_path / "agent_outbox/delivered.json")
    monkeypatch.setattr(notify, "_daily_message_type", lambda: "DAILY_SYNTHESIS")
    monkeypatch.setattr(notify, "_analysis_synthesis_text", lambda _: "Validated fixture brief")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / f"{OLD}.json").write_text('{"sections": {}}')
    budget = tmp_path / "learning/review_dispatch/2026-09-18.json"
    before = budget.read_bytes()
    for index in range(2):
        notify.cmd_prepare(OLD, expected_date=OLD)
        result = json.loads(capsys.readouterr().out)
        assert result["queued"] == ([f"{OLD}:DAILY_SYNTHESIS"] if index == 0 else [])
        assert result["pending_total"] == 1
    assert budget.read_bytes() == before

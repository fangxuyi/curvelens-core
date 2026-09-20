"""Failures must not be reported as successful daily work or fresh delivery."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ccvm.reference.product import load_product
from ccvm.workflow.finalize import AnalysisValidationError
from ccvm.workflow.orchestration import initialize_state, save_state
from ccvm.workflow.packets import build_analysis_packets

REPO = Path(__file__).resolve().parents[2]
DATE = "2026-09-18"
ARTIFACTS = {"analysis_json": "analysis.json", "analysis_md": "analysis.md",
             "statistics_md": "statistics.md", "mobile_md": "mobile.md"}


def module(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "agent" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def completed(root, product="wti", digests=True):
    run = root / "analysis_workflow" / f"trade_date={DATE}"
    quality = {"overall_status": "PASS", "futures": {"status": "PASS"},
               "options": {"status": "PASS"}}
    build_analysis_packets(product=load_product(product), trade_date=DATE,
                           report={"sections": {}}, quality=quality, articles=[], output_dir=run)
    path, state = initialize_state(manifest_path=run / "manifest.json", quality=quality,
                                   quality_attempts=[], repo_root=REPO)
    state["phase"] = "COMPLETE"
    output = root / "analysis" / f"trade_date={DATE}"
    output.mkdir(parents=True)
    for key, filename in ARTIFACTS.items():
        artifact = output / filename
        artifact.write_text(json.dumps({k: state[k] for k in ("product", "trade_date", "packet_id")})
                            if key == "analysis_json" else f"{product} {DATE} validated {key}")
        state[key] = str(artifact)
    if digests:
        state["artifact_sha256"] = {
            key: hashlib.sha256(Path(state[key]).read_bytes()).hexdigest() for key in ARTIFACTS
        }
    save_state(path, state)
    return path, state


def invoke(root, product="wti", command="start"):
    proc = subprocess.run(
        [sys.executable, str(REPO / "agent/analysis_orchestrator.py"), command,
         "--date", DATE, "--expected-date", DATE],
        env={**os.environ, "CCVM_PRODUCT": product, "CCVM_DATA_DIR": str(root)},
        capture_output=True, text=True, timeout=30,
    )
    return proc, json.loads(proc.stdout)


@pytest.mark.parametrize("product", ["wti", "gold", "corn", "silver"])
def test_completed_run_with_missing_report_is_not_success(tmp_path, product):
    path, state = completed(tmp_path, product)
    Path(state["analysis_md"]).unlink()
    before = path.read_bytes()
    for command in ("start", "advance", "status", "inspect"):
        proc, result = invoke(tmp_path, product, command)
        assert proc.returncode == 1
        assert result["result"] == "ORCHESTRATION_ERROR"
        assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("product", "gold"), ("trade_date", "2026-09-17")])
def test_matching_cli_dates_cannot_hide_wrong_saved_identity(tmp_path, field, value):
    path, state = completed(tmp_path)
    state[field] = value
    save_state(path, state)
    proc, result = invoke(tmp_path)
    assert proc.returncode == 1 and result["result"] == "ORCHESTRATION_ERROR"


@pytest.mark.parametrize("artifact", list(ARTIFACTS))
def test_changed_completed_artifact_is_rejected(tmp_path, artifact):
    _, state = completed(tmp_path)
    path = Path(state[artifact])
    path.write_text(path.read_text() + "\n ")
    proc, result = invoke(tmp_path)
    assert proc.returncode == 1 and result["result"] == "ORCHESTRATION_ERROR"


def test_preparation_nonzero_exit_cannot_claim_ready(monkeypatch):
    controller = module("analysis_orchestrator")
    monkeypatch.setattr(controller.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=9, stdout=json.dumps({"result": "ANALYSIS_PACKETS_READY"}), stderr=""))
    with pytest.raises(AnalysisValidationError, match="exit"):
        controller._prepare(DATE)


@pytest.mark.parametrize("contents", ["{truncated", "{}", '[{"text":"no id"}]'])
def test_corrupt_delivery_ledger_never_becomes_an_empty_queue(tmp_path, contents):
    notify = module("notify")
    path = tmp_path / "delivered.json"
    path.write_text(contents)
    with pytest.raises(ValueError):
        notify._load(path)
    assert path.read_text() == contents


@pytest.mark.parametrize("product", ["wti", "gold", "corn", "silver", "copper", "brent"])
@pytest.mark.parametrize("digests", [True, False])
def test_healthy_completed_runs_remain_resumable(tmp_path, product, digests):
    path, state = completed(tmp_path, product, digests)
    before = path.read_bytes()
    proc, result = invoke(tmp_path, product)
    assert proc.returncode == 0 and result["result"] == "ORCHESTRATION_COMPLETE"
    assert result["artifact_integrity"] == ("sha256_verified" if digests else "legacy_identity_and_presence")
    assert result["run_id"] == state["run_id"] and path.read_bytes() == before


def test_legacy_identity_mismatch_is_rejected_without_digests(tmp_path):
    _, state = completed(tmp_path, digests=False)
    report = Path(state["analysis_json"])
    data = json.loads(report.read_text()); data["trade_date"] = "2026-09-17"
    report.write_text(json.dumps(data))
    proc, result = invoke(tmp_path)
    assert proc.returncode == 1 and "identity" in result["detail"]


def test_corrupt_report_blocks_notification_without_touching_outbox(tmp_path, monkeypatch, capsys):
    _, state = completed(tmp_path)
    Path(state["mobile_md"]).write_text("altered report")
    notify = module("notify")
    monkeypatch.setattr(notify, "DATA_DIR", tmp_path)
    monkeypatch.setattr(notify, "_product", lambda: load_product("wti"))
    pending = tmp_path / "agent_outbox/pending.json"
    pending.parent.mkdir(); pending.write_text('[{"id":"existing"}]')
    before = pending.read_bytes()
    monkeypatch.setattr(notify, "PENDING_PATH", pending)
    with pytest.raises(SystemExit) as exc:
        notify.cmd_prepare(DATE, DATE)
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["result"] == "ANALYSIS_NOT_COMPLETE"
    assert pending.read_bytes() == before


def test_ack_interruption_preserves_receipt_and_prevents_resend(tmp_path, monkeypatch, capsys):
    notify = module("notify")
    pending, delivered = tmp_path / "pending.json", tmp_path / "delivered.json"
    pending.write_text('[{"id":"report-one","text":"validated report"}]')
    monkeypatch.setattr(notify, "PENDING_PATH", pending)
    monkeypatch.setattr(notify, "DELIVERED_PATH", delivered)
    real_save = notify._save
    def fail_pending(path, items):
        if path == pending:
            raise OSError("simulated interruption while pruning queue")
        real_save(path, items)
    monkeypatch.setattr(notify, "_save", fail_pending)
    with pytest.raises(OSError):
        notify.cmd_ack(["report-one"], False)
    assert json.loads(delivered.read_text())[0]["id"] == "report-one"
    notify.cmd_list_pending()
    assert json.loads(capsys.readouterr().out)["items"] == []
    monkeypatch.setattr(notify, "_save", real_save)
    notify.cmd_ack(["report-one"], False)
    assert len(json.loads(delivered.read_text())) == 1
    assert json.loads(pending.read_text()) == []


def test_atomic_publication_failure_keeps_previous_file(tmp_path, monkeypatch):
    from ccvm.storage.atomic import write_text_atomic
    path = tmp_path / "report.md"
    path.write_text("previous report")
    def fail(*args):
        raise OSError("simulated publish failure")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        write_text_atomic(path, "new report")
    assert path.read_text() == "previous report"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("phase", ["COMPLETED", None, []])
def test_unknown_phase_cannot_look_like_active_work(tmp_path, phase):
    path, state = completed(tmp_path)
    state["phase"] = phase
    save_state(path, state)
    proc, result = invoke(tmp_path)
    assert proc.returncode == 1 and result["result"] == "ORCHESTRATION_ERROR"
    assert "phase" in result["detail"]


@pytest.mark.parametrize("missing_artifact", [False, True])
def test_controller_persists_completion_only_with_all_digests(
    tmp_path, monkeypatch, capsys, missing_artifact,
):
    path, state = completed(tmp_path, digests=False)
    state["phase"] = "READY_TO_FINALIZE"
    save_state(path, state)
    if missing_artifact:
        Path(state["mobile_md"]).unlink()
    controller = module("analysis_orchestrator")
    monkeypatch.setattr(controller, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "get_product", lambda: load_product("wti"))
    monkeypatch.setattr(controller, "advance_state", lambda *args: state)
    monkeypatch.setattr(controller, "validate_and_render", lambda *args: tuple(
        Path(state[key]) for key in ARTIFACTS
    ))
    monkeypatch.setattr(sys, "argv", ["analysis_orchestrator.py", "advance",
                                     "--date", DATE, "--expected-date", DATE])
    with pytest.raises(SystemExit) as exc:
        controller.main()
    result = json.loads(capsys.readouterr().out)
    saved = json.loads(path.read_text())
    if missing_artifact:
        assert exc.value.code == 1 and result["result"] == "ORCHESTRATION_ERROR"
        assert saved["phase"] == "READY_TO_FINALIZE"
        assert "artifact_sha256" not in saved
    else:
        assert exc.value.code == 0 and result["result"] == "ORCHESTRATION_COMPLETE"
        assert saved["phase"] == "COMPLETE"
        assert saved["artifact_sha256"] == {
            key: hashlib.sha256(Path(state[key]).read_bytes()).hexdigest()
            for key in ARTIFACTS
        }

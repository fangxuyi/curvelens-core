"""Regression coverage for reviewed email handoff and receipt-based recovery."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ccvm.reporting.email_outbox import EmailOutbox, report_for_email
from tests.test_workflow_recovery_guards import completed, DATE

RECIPIENT = "owner@example.com"
IDENTITY = "scheduled-invocation-1"


def setup(root, product="silver"):
    path, state = completed(root, product)
    invocation = root / "automation_invocations" / IDENTITY / "invocation.json"
    invocation.parent.mkdir(parents=True)
    invocation.write_text(json.dumps({
        "invocation_id": IDENTITY, "actual_trade_date": DATE, "expected_trade_date": DATE,
        "gmail_account": RECIPIENT, "run_time_ny": "2026-09-19T05:00:00-04:00",
        "bulletin_vintage": "PRELIMINARY",
    }))
    return EmailOutbox(root, product), path, state, invocation


def prepare(outbox):
    return outbox.prepare(IDENTITY, RECIPIENT, "user request in host thread")


@pytest.mark.parametrize("product", ["wti", "gold", "corn", "silver", "copper", "brent"])
def test_report_is_queued_before_external_send_and_ack_is_idempotent(tmp_path, product):
    outbox, _, state, _ = setup(tmp_path, product)
    record = prepare(outbox)
    assert record["result"] == "EMAIL_QUEUED"
    args = record["gmail_args"]
    assert args["to"] == "me" and args["from_address"] == RECIPIENT
    assert "bcc" not in args and "cc" not in args
    body = args["payload"]["body"]["content"]
    assert "PRELIMINARY" in body and DATE in body and IDENTITY in body
    assert Path(state["analysis_md"]).read_text() in body
    assert record == prepare(outbox)
    assert outbox.ack(IDENTITY, "abc123")["result"] == "EMAIL_ACKNOWLEDGED"
    assert outbox.pending(IDENTITY)["result"] == "EMAIL_ALREADY_DELIVERED"
    assert "gmail_args" not in outbox.pending(IDENTITY)
    assert prepare(outbox)["result"] == "EMAIL_ALREADY_DELIVERED"
    assert outbox.ack(IDENTITY, "abc123")["result"] == "EMAIL_ALREADY_DELIVERED"
    with pytest.raises(ValueError, match="different Gmail"):
        outbox.ack(IDENTITY, "def456")


@pytest.mark.parametrize("mutation", ["stale", "target_changed", "incomplete", "wrong_product",
                                     "altered_artifact", "missing_artifact", "legacy"])
def test_invalid_workflow_never_queues_mail(tmp_path, mutation):
    outbox, state_path, state, invocation_path = setup(tmp_path)
    if mutation in {"stale", "target_changed"}:
        invocation = json.loads(invocation_path.read_text())
        invocation["expected_trade_date"] = "2026-09-21"
        invocation_path.write_text(json.dumps(invocation))
    elif mutation == "incomplete":
        state["phase"] = "SYNTHESIS_REQUIRED"
    elif mutation == "wrong_product":
        state["product"] = "gold"
    elif mutation == "altered_artifact":
        Path(state["analysis_md"]).write_text("unvalidated report")
    elif mutation == "missing_artifact":
        Path(state["statistics_md"]).unlink()
    elif mutation == "legacy":
        del state["artifact_sha256"]
    state_path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        prepare(outbox)
    assert not (tmp_path / "email_outbox").exists()


def test_pending_rechecks_source_integrity_before_sending(tmp_path):
    outbox, _, state, _ = setup(tmp_path)
    prepare(outbox)
    Path(state["analysis_md"]).write_text("changed since queued")
    with pytest.raises(ValueError, match="changed"):
        outbox.pending(IDENTITY)


def test_confirmed_receipt_prevents_resend_even_after_report_changes(tmp_path):
    outbox, _, state, _ = setup(tmp_path)
    prepare(outbox)
    outbox.ack(IDENTITY, "abc123")
    Path(state["analysis_md"]).unlink()
    assert outbox.pending(IDENTITY)["result"] == "EMAIL_ALREADY_DELIVERED"


@pytest.mark.parametrize("recipient", ["stranger@example.com", "a@example.com,b@example.com",
                                      "a@example.com\nBcc: b@example.com"])
def test_unconfigured_or_multiple_recipients_are_rejected(tmp_path, recipient):
    outbox, *_ = setup(tmp_path)
    with pytest.raises(ValueError):
        outbox.prepare(IDENTITY, recipient, "user request")


def test_altered_queue_and_corrupt_receipt_fail_closed(tmp_path):
    outbox, *_ = setup(tmp_path)
    prepare(outbox)
    path = outbox.directory / f"{IDENTITY}.json"
    original = path.read_text()
    record = json.loads(original)
    record["gmail_args"]["to"] = "unexpected@example.com"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="changed"):
        outbox.pending(IDENTITY)
    path.write_text(original)
    receipt = outbox.directory / f"{IDENTITY}.receipt.json"
    receipt.write_text("{truncated")
    with pytest.raises(ValueError):
        prepare(outbox)
    assert receipt.read_text() == "{truncated"


def test_failed_ack_write_leaves_queue_recoverable(tmp_path, monkeypatch):
    outbox, *_ = setup(tmp_path)
    prepare(outbox)
    import ccvm.reporting.email_outbox as module
    def interrupted(*args):
        raise OSError("interrupted receipt write")
    monkeypatch.setattr(module, "write_text_atomic", interrupted)
    with pytest.raises(OSError):
        outbox.ack(IDENTITY, "abc123")
    assert outbox.pending(IDENTITY)["result"] == "EMAIL_QUEUED"
    # Host must reconcile Gmail Sent before retrying an unacknowledged send.


def test_citations_keep_identity_and_local_paths_block_export():
    assert report_for_email("knowledge:knowledge/silver/macro.md:abc123") == "knowledge:silver:macro:abc123"
    assert report_for_email("knowledge:knowledge/wti/calendar.yaml:6e1467e6da7b") == "knowledge:wti:calendar:6e1467e6da7b"
    for report in ["see /Users/operator/private.txt", "file:///tmp/report", "ccvm/data/report", "/etc/passwd"]:
        with pytest.raises(ValueError, match="local paths"):
            report_for_email(report)


def test_report_math_and_percent_units_are_preserved_without_allowing_paths():
    report = "Slope (4021.5-3853)/10. Carry (3870-3853)/3853 times 12; -9.136490%/year. Corn -(4.5925-4.4675)/4.4675 × 12."
    assert report_for_email(report) == report
    for unsafe in ["(value)/10/private", "(value)/10.5/private", "(value)/4.4675/private", "%/year/private", "/year", "/10", "/4.4675", "knowledge/wti/calendar.yaml", report + " /etc/passwd"]:
        with pytest.raises(ValueError, match="local paths"):
            report_for_email(unsafe)


def test_no_authorization_reference_or_path_traversal(tmp_path):
    outbox, *_ = setup(tmp_path)
    with pytest.raises(ValueError, match="authorization"):
        outbox.prepare(IDENTITY, RECIPIENT, "")
    with pytest.raises(ValueError, match="invocation ID"):
        outbox.pending("../other-product")


def test_stale_source_queues_only_explicit_status_without_report(tmp_path):
    outbox, _, state, invocation_path = setup(tmp_path)
    invocation = json.loads(invocation_path.read_text())
    invocation["expected_trade_date"] = "2026-09-21"
    invocation_path.write_text(json.dumps(invocation))
    queued = outbox.prepare_status(IDENTITY, RECIPIENT, "user request", "blocked",
                                   "Acquired bulletin is older than the frozen target.")
    assert queued["payload_type"] == "status"
    assert Path(state["analysis_md"]).read_text() not in queued["gmail_args"]["payload"]["body"]["content"]
    assert outbox.ack(IDENTITY, "abc123")["result"] == "EMAIL_ACKNOWLEDGED"


def test_status_rejects_raw_paths_and_unbounded_payload(tmp_path):
    outbox, *_ = setup(tmp_path)
    for summary in ["", "x" * 1001, "Read /Users/operator/secret.txt"]:
        with pytest.raises(ValueError):
            outbox.prepare_status(IDENTITY, RECIPIENT, "user request", "blocked", summary)
    assert not outbox.directory.exists()


def test_changed_vintage_blocks_pending(tmp_path):
    outbox, _, _, invocation_path = setup(tmp_path)
    prepare(outbox)
    invocation = json.loads(invocation_path.read_text())
    invocation["bulletin_vintage"] = "FINAL"
    invocation_path.write_text(json.dumps(invocation))
    with pytest.raises(ValueError, match="no longer matches"):
        outbox.pending(IDENTITY)


def test_prepare_cannot_change_queued_payload_type(tmp_path):
    outbox, *_ = setup(tmp_path)
    prepare(outbox)
    with pytest.raises(ValueError, match="payload type"):
        outbox.prepare_status(IDENTITY, RECIPIENT, "user request", "blocked", "Delivery blocked.")


def test_cli_requires_explicit_product_and_emits_recoverable_json(tmp_path):
    setup(tmp_path)
    command = [sys.executable, str(Path(__file__).resolve().parents[2] / "agent/email_outbox.py"),
               "prepare", "--invocation-id", IDENTITY, "--recipient", RECIPIENT,
               "--authorization-reference", "user request"]
    env = {**os.environ, "CCVM_DATA_DIR": str(tmp_path)}
    env.pop("CCVM_PRODUCT", None)
    missing = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["result"] == "EMAIL_OUTBOX_BLOCKED"
    env["CCVM_PRODUCT"] = "silver"
    queued = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert queued.returncode == 0
    assert json.loads(queued.stdout)["result"] == "EMAIL_QUEUED"

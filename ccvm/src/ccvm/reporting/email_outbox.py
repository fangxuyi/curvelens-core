"""Validated, product-isolated Gmail handoff; this module never sends mail.

Authorization remains the host's responsibility. Queueing is evidence of the
exact payload, not permission to send it. One host sender may operate a product
at a time, and must check Gmail Sent before sending or recovering an ambiguous send.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re

from ccvm.storage.atomic import write_text_atomic
from ccvm.workflow.completion import validate_run_identity, verify_completed_artifacts
from ccvm.workflow.freshness import trade_date_guard


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Email outbox record must be an object")
    return value


def _save(path: Path, value: dict) -> None:
    write_text_atomic(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", value):
        raise ValueError("Invalid invocation ID")
    return value


def _mailbox(value: str) -> str:
    if not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value):
        raise ValueError("Exactly one mailbox address is required")
    return value


def report_for_email(report: str) -> str:
    # Keep citations auditable without disclosing repository paths. No narrative,
    # numbers, limitations, or reference hashes are removed.
    report = re.sub(r"knowledge:knowledge/([a-z0-9_-]+)/([a-z0-9_-]+)\.md:",
                    r"knowledge:\1:\2:", report)
    if re.search(r"(?:file://|[A-Z]:\\|ccvm/data/|knowledge/|(?<![\w:/])/(?!/)[^\s<>()]+)", report):
        raise ValueError("Report contains local paths; preserve it locally for review")
    return report


class EmailOutbox:
    def __init__(self, root: Path, product: str):
        self.root = root
        self.product = product
        self.directory = root / "email_outbox"

    def _paths(self, invocation_id: str) -> tuple[Path, Path]:
        identity = _safe_id(invocation_id)
        return (self.directory / f"{identity}.json",
                self.directory / f"{identity}.receipt.json")

    def _context(self, invocation_id: str) -> tuple[dict, dict]:
        _safe_id(invocation_id)
        invocation = _read(self.root / "automation_invocations" / invocation_id / "invocation.json")
        if invocation.get("invocation_id") != invocation_id:
            raise ValueError("Invocation identity mismatch")
        # Existing invocation records need not contain product; when present it
        # must agree. The directory and workflow independently bind the product.
        if invocation.get("product", self.product) != self.product:
            raise ValueError("Invocation product mismatch")
        actual, expected = invocation["actual_trade_date"], invocation["expected_trade_date"]
        if trade_date_guard(actual, expected) or expected is None:
            raise ValueError("Report date does not match the frozen invocation target")
        run_dir = self.root / "analysis_workflow" / f"trade_date={actual}"
        state = _read(run_dir / "run.json")
        validate_run_identity(state, self.product, actual, run_dir)
        if verify_completed_artifacts(state, self.root) != "sha256_verified":
            raise ValueError("Email requires a completed report with verified artifact digests")
        return invocation, state

    def prepare(self, invocation_id: str, recipient: str, authorization_reference: str) -> dict:
        queue_path, receipt_path = self._paths(invocation_id)
        recipient = _mailbox(recipient)
        if not authorization_reference.strip():
            raise ValueError("An authorization reference is required; it does not grant permission")
        # Existing queued content is immutable, including recipient and consent
        # reference. Repeated preparation must not silently replace a reviewed mail.
        if queue_path.exists():
            queued = self.pending(invocation_id)
            if queued["recipient"] != recipient:
                raise ValueError("Invocation already queued for a different recipient")
            if queued["result"] == "EMAIL_QUEUED" and queued["payload_type"] != "full_report":
                raise ValueError("Invocation already queued with a different payload type")
            return queued
        if receipt_path.exists():
            raise ValueError("Receipt exists without queue record; preserve and reconcile it")
        invocation, state = self._context(invocation_id)
        if invocation.get("gmail_account") != recipient:
            raise ValueError("Recipient does not match the invocation's configured mailbox")
        run_time = invocation["run_time_ny"]
        if datetime.fromisoformat(run_time).tzinfo is None:
            raise ValueError("Invocation run time must include its timezone")
        vintage = invocation["bulletin_vintage"]
        if vintage not in {"PRELIMINARY", "FINAL"}:
            raise ValueError("Unknown bulletin vintage")
        report = report_for_email(Path(state["analysis_md"]).read_text())
        actual, expected = invocation["actual_trade_date"], invocation["expected_trade_date"]
        body = (f"CurveLens {self.product.upper()} complete validated report\n"
                f"Actual trade date: {actual}\nExpected trade date: {expected}\n"
                f"Workflow: ORCHESTRATION_COMPLETE\nBulletin: {vintage}\n"
                f"Run time (New York): {run_time}\nDelivery ID: {invocation_id}\n"
                "Delivery is separately authorized; the report's automatic-delivery note refers to the analysis controller.\n\n"
                + report)
        args = {
            "from_address": recipient, "to": "me",
            "subject": f"CurveLens {self.product.upper()} — successful — {actual} — {invocation_id}",
            "payload": {"mime_type": "text/html", "charset": "utf-8", "body": {
                "content": '<div style="white-space:pre-wrap;font-family:Arial,sans-serif">'
                           + html.escape(body) + "</div>"}},
        }
        record = {
            "schema_version": 1, "product": self.product, "invocation_id": invocation_id,
            "payload_type": "full_report", "run_time_ny": run_time, "bulletin_vintage": vintage,
            "actual_trade_date": actual, "expected_trade_date": expected,
            "workflow_run_id": state["run_id"], "artifact_sha256": state["artifact_sha256"],
            "recipient": recipient, "authorization_reference": authorization_reference,
            "gmail_args": args, "created_at": datetime.now(timezone.utc).isoformat(),
        }
        record["record_sha256"] = _digest(record)
        _save(queue_path, record)
        return self.pending(invocation_id)

    def prepare_status(self, invocation_id: str, recipient: str, authorization_reference: str,
                       status: str, summary: str) -> dict:
        """Queue only a bounded, host-reviewed failure summary; never an old report."""
        queue_path, receipt_path = self._paths(invocation_id)
        recipient = _mailbox(recipient)
        if status not in {"blocked", "failed"} or not summary.strip() or len(summary) > 1000:
            raise ValueError("Use blocked/failed and a concise observed summary of at most 1000 characters")
        summary = report_for_email(summary)
        if not authorization_reference.strip():
            raise ValueError("An authorization reference is required")
        if queue_path.exists():
            queued = self.pending(invocation_id)
            if queued["recipient"] != recipient:
                raise ValueError("Invocation already queued for a different recipient")
            if queued["result"] == "EMAIL_QUEUED" and queued["payload_type"] != "status":
                raise ValueError("Invocation already queued with a different payload type")
            return queued
        if receipt_path.exists():
            raise ValueError("Receipt exists without queue record; preserve and reconcile it")
        invocation = _read(self.root / "automation_invocations" / invocation_id / "invocation.json")
        if (invocation.get("invocation_id") != invocation_id
                or invocation.get("product", self.product) != self.product
                or invocation.get("gmail_account") != recipient):
            raise ValueError("Invocation identity or mailbox mismatch")
        expected = invocation["expected_trade_date"]
        actual = invocation.get("actual_trade_date")
        # Validate dates without requiring equality: a stale source is a valid
        # reason for a status email, never a valid reason to email its full report.
        if not expected:
            raise ValueError("Frozen expected trade date is required")
        trade_date_guard(actual or expected, expected)
        body = (f"CurveLens {self.product.upper()} — {status}\nExpected trade date: {expected}\n"
                f"Actual trade date: {actual or 'unverified'}\nDelivery ID: {invocation_id}\n\n{summary}")
        record = {
            "schema_version": 1, "product": self.product, "invocation_id": invocation_id,
            "payload_type": "status", "actual_trade_date": actual, "expected_trade_date": expected,
            "recipient": recipient, "authorization_reference": authorization_reference,
            "gmail_args": {
                "from_address": recipient, "to": "me",
                "subject": f"CurveLens {self.product.upper()} — {status} — {actual or 'unverified'} — {invocation_id}",
                "payload": {"mime_type": "text/html", "charset": "utf-8", "body": {
                    "content": "<div style=\"white-space:pre-wrap\">" + html.escape(body) + "</div>"}},
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        record["record_sha256"] = _digest(record)
        _save(queue_path, record)
        return self.pending(invocation_id)

    def pending(self, invocation_id: str) -> dict:
        queue_path, receipt_path = self._paths(invocation_id)
        record = _read(queue_path)
        digest = record.pop("record_sha256", None)
        if digest != _digest(record):
            raise ValueError("Queued email content changed or record is corrupt")
        record["record_sha256"] = digest
        if (record.get("schema_version") != 1 or record.get("product") != self.product
                or record.get("invocation_id") != invocation_id):
            raise ValueError("Queued email identity mismatch")
        # A confirmed receipt suppresses retransmission even if report files have
        # subsequently changed. Missing/corrupt receipts fail closed, never reset.
        if receipt_path.exists():
            receipt = _read(receipt_path)
            if (receipt.get("record_sha256") != digest
                    or not isinstance(receipt.get("message_id"), str)
                    or not re.fullmatch(r"[0-9a-f]+", receipt["message_id"])
                    or receipt.get("product") != self.product
                    or receipt.get("invocation_id") != invocation_id):
                raise ValueError("Email receipt does not match the queued report")
            return {"result": "EMAIL_ALREADY_DELIVERED", "recipient": record["recipient"],
                    "receipt": receipt}
        if record["payload_type"] == "full_report":
            invocation, state = self._context(invocation_id)
            if (record["artifact_sha256"] != state["artifact_sha256"]
                    or record["workflow_run_id"] != state["run_id"]
                    or record["run_time_ny"] != invocation["run_time_ny"]
                    or record["bulletin_vintage"] != invocation["bulletin_vintage"]):
                raise ValueError("Queued report no longer matches the validated invocation")
        elif record["payload_type"] == "status":
            invocation = _read(self.root / "automation_invocations" / invocation_id / "invocation.json")
        else:
            raise ValueError("Unknown email payload type")
        if (invocation.get("invocation_id") != invocation_id
                or invocation.get("product", self.product) != self.product
                or record["actual_trade_date"] != invocation.get("actual_trade_date")
                or record["expected_trade_date"] != invocation["expected_trade_date"]
                or record["recipient"] != invocation["gmail_account"]):
            raise ValueError("Queued email no longer matches the validated invocation")
        return {"result": "EMAIL_QUEUED", **record}

    def ack(self, invocation_id: str, message_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]+", message_id):
            raise ValueError("A confirmed Gmail message ID is required")
        queued = self.pending(invocation_id)
        if queued["result"] == "EMAIL_ALREADY_DELIVERED":
            if queued["receipt"]["message_id"] != message_id:
                raise ValueError("Invocation already acknowledged with a different Gmail ID")
            return queued
        receipt = {
            "product": self.product, "invocation_id": invocation_id,
            "record_sha256": queued["record_sha256"], "message_id": message_id,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(),
        }
        # Retain immutable payload and write receipt atomically: no pruning window
        # can expose an acknowledged message for resend.
        _save(self._paths(invocation_id)[1], receipt)
        return {"result": "EMAIL_ACKNOWLEDGED", "receipt": receipt}

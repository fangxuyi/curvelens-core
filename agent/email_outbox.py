#!/usr/bin/env python
"""Prepare/inspect/acknowledge authorized Gmail payloads; never sends email."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ccvm/src"))
from ccvm.reference.product import load_product
from ccvm.reporting.email_outbox import EmailOutbox
from ccvm.runtime import data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--recipient", required=True)
    prepare.add_argument("--authorization-reference", required=True)
    status = sub.add_parser("prepare-status")
    status.add_argument("--recipient", required=True)
    status.add_argument("--authorization-reference", required=True)
    status.add_argument("--status", choices=["blocked", "failed"], required=True)
    status.add_argument("--summary", required=True)
    pending = sub.add_parser("pending")
    ack = sub.add_parser("ack")
    ack.add_argument("--message-id", required=True)
    for command in (prepare, status, pending, ack):
        command.add_argument("--invocation-id", required=True)
    args = parser.parse_args()
    try:
        product = os.environ.get("CCVM_PRODUCT")
        if not product:
            raise ValueError("Explicit CCVM_PRODUCT required")
        load_product(product)
        outbox = EmailOutbox(data_dir(product), product)
        outbox.directory.mkdir(parents=True, exist_ok=True)
        # Serialize local mutations. The host must still serialize the complete
        # pending -> Gmail Sent search -> send -> ack cycle across workers.
        with (outbox.directory / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.command == "prepare":
                result = outbox.prepare(args.invocation_id, args.recipient, args.authorization_reference)
            elif args.command == "prepare-status":
                result = outbox.prepare_status(args.invocation_id, args.recipient,
                                              args.authorization_reference, args.status, args.summary)
            elif args.command == "pending":
                result = outbox.pending(args.invocation_id)
            else:
                result = outbox.ack(args.invocation_id, args.message_id)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"result": "EMAIL_OUTBOX_BLOCKED", "detail": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

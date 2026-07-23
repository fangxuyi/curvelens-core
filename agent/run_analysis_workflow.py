#!/usr/bin/env python
"""Prepare specialist evidence packets for an agent-framework analysis run.

This command performs no model calls. It separates market collection/QC from
news collection, computes reproducible features, and emits packet paths that an
OpenClaw/OpenAI-framework coordinator delegates to specialist sub-agents.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).parent.parent
CCVM_DIR = REPO_ROOT / "ccvm"
SCRIPTS = CCVM_DIR / "scripts"
sys.path.insert(0, str(CCVM_DIR / "src"))

from ccvm.collectors.rss import find_raw_articles
from ccvm.reference.product import get_product
from ccvm.runtime import data_dir
from ccvm.workflow import assess_quality, build_analysis_packets, load_articles


def _emit(value: dict, ok: bool = True) -> None:
    print(json.dumps(value))
    raise SystemExit(0 if ok else 1)


def _run(script: str, *args: str) -> int:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=str(CCVM_DIR), stdout=sys.stderr,
    )
    return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today ET)")
    parser.add_argument("--max-quality-attempts", type=int)
    parser.add_argument("--force-pdf", action="store_true")
    args = parser.parse_args()
    try:
        as_of = date.fromisoformat(args.date) if args.date else datetime.now(
            ZoneInfo("America/New_York")
        ).date()
    except ValueError:
        _emit({"result": "ERROR", "step": "args", "detail": "invalid date"}, False)
    as_of_str = as_of.isoformat()
    product = get_product()
    max_attempts = max(1, args.max_quality_attempts or product.analysis_max_quality_attempts)
    root = data_dir()
    if (
        product.market_data is not None
        and product.market_data.provider == "authorized_files"
    ):
        required = product.market_data.required_paths(root, as_of_str)
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            _emit({
                "result": "NEED_AUTHORIZED_MARKET_DATA",
                "date": as_of_str,
                "provider": product.market_data.provider,
                "missing_paths": missing,
                "credentials_env": list(product.market_data.credentials_env),
            })
    pdf_path = root / "cme_bulletin" / f"{as_of_str}.pdf"
    if product.bulletin and not pdf_path.exists() and not args.force_pdf:
        _emit({
            "result": "NEED_CME_PDF", "date": as_of_str,
            "pdf_path": str(pdf_path), "url": product.bulletin.url,
        })

    history = []
    quality_path = root / "quality_reports" / f"{as_of_str}.json"
    for attempt in range(1, max_attempts + 1):
        collect_rc = _run("collect_day.py", "--date", as_of_str, "--source", "market")
        # Never accept a quality file left by a prior invocation when this
        # normalization attempt fails before producing a new report.
        quality_path.unlink(missing_ok=True)
        normalize_rc = _run("normalize_day.py", "--date", as_of_str, "--force")
        if not quality_path.exists():
            _emit({"result": "DATA_REVIEW_REQUIRED", "date": as_of_str,
                   "step": "normalize", "detail": "quality report was not produced"}, False)
        quality = json.loads(quality_path.read_text())
        decision = assess_quality(
            quality, attempt, max_attempts,
            blocking_sections=product.analysis_blocking_sections,
            retryable_empty_sections=product.analysis_retryable_empty_sections,
        )
        decision["collect_exit_code"] = collect_rc
        decision["normalize_exit_code"] = normalize_rc
        history.append(decision)
        if not decision["should_retry"]:
            break

    if decision["disposition"] == "BLOCKED":
        _emit({"result": "DATA_REVIEW_REQUIRED", "date": as_of_str,
               "quality": decision, "attempts": history}, False)

    if _run("compute_features.py", "--date", as_of_str) != 0:
        _emit({"result": "DATA_REVIEW_REQUIRED", "date": as_of_str,
               "step": "compute", "attempts": history}, False)
    # Compute may add model diagnostics (for example RND) to the report.
    quality = json.loads(quality_path.read_text())
    final_quality = assess_quality(
        quality, decision["attempt"], decision["attempt"],
        blocking_sections=product.analysis_blocking_sections,
        retryable_empty_sections=product.analysis_retryable_empty_sections,
    )

    # News is deliberately collected only after market evidence is usable.
    news_rc = _run("collect_day.py", "--date", as_of_str, "--source", "news")
    if _run("generate_report.py", "--date", as_of_str) != 0:
        _emit({"result": "ERROR", "date": as_of_str, "step": "report"}, False)
    report_path = root / "reports" / f"{as_of_str}.json"
    if not report_path.exists():
        _emit({"result": "ERROR", "date": as_of_str,
               "step": "report", "detail": "report JSON missing"}, False)

    article_path = find_raw_articles(root, as_of)
    packet_dir = root / "analysis_workflow" / f"trade_date={as_of_str}"
    manifest = build_analysis_packets(
        product=product, trade_date=as_of_str,
        report=json.loads(report_path.read_text()), quality=quality,
        articles=load_articles(article_path), output_dir=packet_dir,
    )
    stage_failures = [
        {"attempt": item.get("attempt"), "collect_exit_code": item.get("collect_exit_code"),
         "normalize_exit_code": item.get("normalize_exit_code")}
        for item in history
        if item.get("collect_exit_code") != 0 or item.get("normalize_exit_code") != 0
    ]
    limited = final_quality["disposition"] != "READY" or news_rc != 0 or bool(stage_failures)
    _emit({
        "result": "ANALYSIS_PACKETS_READY_WITH_LIMITATIONS" if limited else "ANALYSIS_PACKETS_READY",
        "date": as_of_str, "manifest": str(packet_dir / "manifest.json"),
        "roles": manifest["roles"], "quality": final_quality,
        "quality_report": quality,
        "quality_attempts": history, "news_status": "ok" if news_rc == 0 else "failed",
        "preparation_warnings": stage_failures,
        "next_step": "Delegate each role packet through the agent framework, then synthesize and run finalize_analysis.py.",
        "workflow_mode": "agent_orchestrated", "delivery_queued": False,
    })


if __name__ == "__main__":
    main()

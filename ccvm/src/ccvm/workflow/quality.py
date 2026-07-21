"""Turn deterministic quality reports into workflow decisions."""
from __future__ import annotations

from typing import Any


def assess_quality(report: dict[str, Any], attempt: int, max_attempts: int) -> dict[str, Any]:
    """Classify quality findings without pretending that all failures are repairable.

    Missing market inputs are retryable because another collection attempt may
    recover them. Invalid rows and model diagnostics are retained as explicit
    limitations for the relevant analyst; they are never silently repaired.
    """
    issues: list[dict[str, Any]] = []
    retry_sections: list[str] = []
    for key in ("futures", "options", "fundamentals", "macro", "rnd"):
        section = report.get(key)
        if not isinstance(section, dict):
            continue
        status = str(section.get("status", "UNKNOWN"))
        if status == "PASS":
            continue
        count = int(section.get("record_count", 0) or 0)
        retryable = key in {"futures", "options"} and count == 0
        issues.append({
            "section": key,
            "status": status,
            "notes": list(section.get("notes", [])),
            "retryable": retryable,
        })
        if retryable:
            retry_sections.append(key)

    futures = report.get("futures", {})
    usable_futures = int(futures.get("record_count", 0) or 0) > 0
    should_retry = bool(retry_sections) and attempt < max_attempts
    if not usable_futures and not should_retry:
        disposition = "BLOCKED"
    elif issues:
        disposition = "READY_WITH_LIMITATIONS"
    else:
        disposition = "READY"
    return {
        "attempt": attempt,
        "max_attempts": max_attempts,
        "disposition": disposition,
        "should_retry": should_retry,
        "retry_sections": retry_sections,
        "issues": issues,
    }

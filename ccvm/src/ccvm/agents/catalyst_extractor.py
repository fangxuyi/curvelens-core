"""Deprecated compatibility surface for the removed direct model extractor."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Optional


class DirectModelInvocationDisabled(RuntimeError):
    """Repository code must delegate model work through the host framework."""


def _stable_event_id(title: str, effective_start: Optional[str], event_type: str) -> str:
    canonical = f"{event_type}|{title}|{effective_start or 'null'}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def extract(
    text: str, source_url: str, published_at: str, observation_date: date,
    api_key: Optional[str] = None, model: str = "",
) -> Optional[dict]:
    """Reject the former CLI path; use the agent-framework workflow instead."""
    raise DirectModelInvocationDisabled(
        "Direct catalyst model invocation was removed. Run "
        "agent/run_analysis_workflow.py and delegate its role packets through "
        "the host agent framework."
    )

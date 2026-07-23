"""Render a concise phone-first brief from validated CurveLens analysis."""
from __future__ import annotations

import re
from typing import Any

MAX_MOBILE_BRIEF_CHARS = 2800


def _clean(value: Any) -> str:
    text = re.sub(r"\s*\[(?:feature|knowledge|news):[^\]]+\]", "", str(value or ""))
    return " ".join(text.replace("**", "").replace("__", "").split()).strip()


def _short(value: Any, limit: int) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    clipped = text[:limit - 1].rsplit(" ", 1)[0].rstrip()
    return (clipped or text[:limit - 1].rstrip()) + "…"


def _metrics_line(metrics: list[dict[str, Any]]) -> str:
    parts = []
    for metric in metrics[:2]:
        label = _short(metric.get("label", "Metric"), 42)
        value = _short(metric.get("value", ""), 44)
        parts.append(f"{label}: {value}")
    return _short("; ".join(parts), 150)


def render_mobile_brief(analysis: dict[str, Any]) -> str:
    """Return one compact Markdown message without changing the synthesis."""
    synthesis = analysis.get("synthesis") or {}
    product = _short(analysis.get("product") or "Market", 30).upper()
    trade_date = _short(analysis.get("trade_date") or "", 20)
    lines = [f"*{product} Daily Brief — {trade_date}*", "", "*Bottom line*"]
    summary = synthesis.get("plain_english_summary") or synthesis.get("executive_summary")
    lines.append(_short(summary, 280) or "No validated summary is available.")

    for index, view in enumerate((synthesis.get("top_views") or [])[:3], start=1):
        rank = view.get("rank") or index
        lines.extend(["", f"*{rank}. {_short(view.get('title'), 70)}*"])
        plain_view = _short(view.get("plain_english_view"), 150)
        if plain_view:
            lines.append(plain_view)
        metrics = _metrics_line(view.get("key_metrics") or [])
        if metrics:
            lines.append(f"Numbers: {metrics}")
        driver = view.get("driver_analysis") or {}
        explanation = _short(driver.get("explanation"), 150)
        if explanation:
            status = _clean(driver.get("status")).replace("_", " ")
            lines.append(f"Driver ({status}): {explanation}")
        conflicts = view.get("conflicting_evidence") or []
        if conflicts:
            item = conflicts[0]
            claim = item.get("claim") if isinstance(item, dict) else item
            lines.append(f"Conflict: {_short(claim, 110)}")
        watch = view.get("what_to_watch") or []
        if watch:
            lines.append(f"Watch: {_short(watch[0], 120)}")

    limitations = synthesis.get("data_limitations") or []
    if limitations:
        lines.extend(["", f"_Data note: {_short(limitations[0], 160)}_"])

    message = "\n".join(lines)
    if len(message) > MAX_MOBILE_BRIEF_CHARS:
        message = message[:MAX_MOBILE_BRIEF_CHARS - 1].rstrip() + "…"
    return message

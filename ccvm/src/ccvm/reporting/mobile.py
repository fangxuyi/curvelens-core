"""Render a concise phone-first brief from validated CurveLens analysis."""
from __future__ import annotations

import re
from typing import Any

MAX_MOBILE_BRIEF_CHARS = 1400


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
    """Return the validated editorial subset of a complete synthesis."""
    synthesis = analysis.get("synthesis") or {}
    product = _short(analysis.get("product") or "Market", 30).upper()
    trade_date = _short(analysis.get("trade_date") or "", 20)
    lines = [f"*{product} Daily Brief — {trade_date}*", "", "*Bottom line*"]
    summary = synthesis.get("plain_english_summary") or synthesis.get("executive_summary")
    lines.append(_short(summary, 240) or "No validated summary is available.")

    views = {
        int(view.get("rank", index)): view
        for index, view in enumerate(synthesis.get("top_views") or [], start=1)
        if isinstance(view, dict)
    }
    selection = synthesis.get("mobile_selection") or {}
    selected_ranks = selection.get("selected_view_ranks")
    if not isinstance(selected_ranks, list):
        selected_ranks = [min(views)] if views else []
    selected_views = [views[rank] for rank in selected_ranks if rank in views][:2]
    if selected_views:
        lines.extend(["", "*What matters now*"])
    for index, view in enumerate(selected_views, start=1):
        lines.extend(["", f"*{index}. {_short(view.get('title'), 68)}*"])
        plain_view = _short(view.get("plain_english_view"), 135)
        if plain_view:
            lines.append(plain_view)
        metrics = _metrics_line(view.get("key_metrics") or [])
        if metrics:
            lines.append(f"Key move: {metrics}")
        conflicts = view.get("conflicting_evidence") or []
        if view.get("evidence_relationship") == "conflicting" and conflicts:
            item = conflicts[0]
            claim = item.get("claim") if isinstance(item, dict) else item
            lines.append(f"Material conflict: {_short(claim, 100)}")
        watch = view.get("what_to_watch") or []
        if watch:
            lines.append(f"Watch: {_short(watch[0], 110)}")

    limitations = synthesis.get("data_limitations") or []
    include_limitation = (
        selection.get("limitation_disposition") == "included"
        if selection else bool(limitations)
    )
    if limitations and include_limitation:
        lines.extend(["", f"_Risk note: {_short(limitations[0], 140)}_"])

    message = "\n".join(lines)
    if len(message) > MAX_MOBILE_BRIEF_CHARS:
        message = message[:MAX_MOBILE_BRIEF_CHARS - 1].rstrip() + "…"
    return message

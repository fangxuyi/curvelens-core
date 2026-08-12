"""Render a concise phone-first brief from validated CurveLens analysis."""
from __future__ import annotations

import re
from typing import Any

MAX_MOBILE_BRIEF_CHARS = 1400
_SENTENCE_END = re.compile(r'[.!?](?:["\')\]]+)?(?=\s+[A-Z0-9]|$)')


def _clean(value: Any) -> str:
    text = re.sub(r"\s*\[(?:feature|knowledge|news):[^\]]+\]", "", str(value or ""))
    return " ".join(text.replace("**", "").replace("__", "").split()).strip()


def _complete_prose(value: Any, limit: int) -> str:
    """Keep complete source sentences that fit; never create a trailing fragment."""
    text = _clean(value)
    if len(text) <= limit:
        return text
    ends = [match.end() for match in _SENTENCE_END.finditer(text) if match.end() <= limit]
    return text[:ends[-1]].rstrip() if ends else ""


def _metrics_line(metrics: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for metric in metrics[:2]:
        label = _clean(metric.get("label", "Metric"))
        value = _clean(metric.get("value", ""))
        part = f"{label}: {value}"
        candidate = "; ".join([*parts, part])
        if len(candidate) <= 220:
            parts.append(part)
    return "; ".join(parts)


def _story_claim(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return _clean(item.get("claim"))


def _fallback_driver(view: dict[str, Any]) -> str:
    driver = view.get("driver_analysis")
    if not isinstance(driver, dict):
        return ""
    return _clean(driver.get("explanation"))


def _compose(lines: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for item in lines:
        if item.get("removed"):
            continue
        line = item["text"]
        if not line and (not rendered or not rendered[-1]):
            continue
        rendered.append(line)
    while rendered and not rendered[-1]:
        rendered.pop()
    return "\n".join(rendered)


def _fit_complete_lines(lines: list[dict[str, Any]]) -> str:
    """Meet the mobile budget by dropping whole optional lines."""
    message = _compose(lines)
    for item in sorted(
        (item for item in lines if item.get("drop_priority") is not None),
        key=lambda item: item["drop_priority"],
    ):
        if len(message) <= MAX_MOBILE_BRIEF_CHARS:
            break
        item["removed"] = True
        message = _compose(lines)
    return message


def render_mobile_brief(analysis: dict[str, Any]) -> str:
    """Return the validated editorial subset of a complete synthesis."""
    synthesis = analysis.get("synthesis") or {}
    product = _clean(analysis.get("product") or "Market")
    product = product.upper() if len(product) <= 30 else "MARKET"
    trade_date = _clean(analysis.get("trade_date") or "")
    lines: list[dict[str, Any]] = []

    def add(text: str, drop_priority: int | None = None) -> None:
        lines.append({"text": text, "drop_priority": drop_priority})

    add(f"*{product} Daily Brief — {trade_date}*")
    add("")
    add("*Bottom line*")
    summary = synthesis.get("plain_english_summary") or synthesis.get("executive_summary")
    add(_complete_prose(summary, 280) or "See the full report for the validated bottom line.")

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
        add("")
        add("*What matters now*")
    for index, view in enumerate(selected_views, start=1):
        title = _clean(view.get("title"))
        if len(title) > 120:
            title = f"Selected market view {index}"
        add("")
        add(f"*{index}. {title}*")
        plain_view = _complete_prose(view.get("plain_english_view"), 220)
        if plain_view:
            add(plain_view, 20 + index)
        story = view.get("story_chain") or {}
        narrative = _complete_prose(
            _story_claim(story.get("narrative_change")) or _fallback_driver(view),
            220,
        )
        if narrative:
            add(f"Why it moved: {narrative}", 30 + index)
        option_read = _complete_prose(
            _story_claim(story.get("option_market_readthrough")), 180,
        )
        if option_read:
            add(f"Options: {option_read}", 35 + index)
        metrics = _metrics_line(view.get("key_metrics") or [])
        if metrics:
            add(f"Key move: {metrics}", 60 + index)
        conflicts = view.get("conflicting_evidence") or []
        if view.get("evidence_relationship") == "conflicting" and conflicts:
            item = conflicts[0]
            claim = item.get("claim") if isinstance(item, dict) else item
            conflict = _complete_prose(claim, 180)
            if conflict:
                add(f"Material conflict: {conflict}", 70 + index)
        watch_item = _complete_prose(
            _story_claim(story.get("forward_watch")), 180,
        )
        if not watch_item:
            watch = view.get("what_to_watch") or []
            if watch:
                watch_item = _complete_prose(watch[0], 180)
        if watch_item:
            add(f"Watch: {watch_item}", 10 + index)

    limitations = synthesis.get("data_limitations") or []
    include_limitation = (
        selection.get("limitation_disposition") == "included"
        if selection else bool(limitations)
    )
    if limitations and include_limitation:
        limitation = _complete_prose(limitations[0], 200)
        if limitation:
            add("")
            add(f"_Risk note: {limitation}_", 90)

    return _fit_complete_lines(lines)

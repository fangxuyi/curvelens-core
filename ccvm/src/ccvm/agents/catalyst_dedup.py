"""
Catalyst dedup, decay, and theme clustering (C5).

The extractor pulls the same story from multiple feeds (the 2026-07-09 store
holds four near-identical Hormuz headlines). Display-level processing, applied
at report time — the store keeps every extraction for lineage:

- dedupe():   token containment on normalized/stemmed titles; near-duplicates collapse to
              the highest-scored instance (survivor carries a dup count)
- apply_decay(): events fade once their effective_start has passed —
              5%/day off the relevance score, floored at 40% of original
- cluster_themes(): (event_type, direction) groups with counts for the brief
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

_CONTAINMENT_THRESHOLD = 0.5
_DECAY_PER_DAY = 0.05
_DECAY_FLOOR = 0.4

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "as", "and", "or", "at",
    "by", "with", "from", "into", "amid", "after", "before", "over", "us",
    "u.s.", "says", "say", "report", "reports", "week", "weekly", "wow",
}


def _stem(w: str) -> str:
    """Light stemming: flows→flow, strikes→strike (plural/3rd-person s)."""
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _tokens(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9']+", (title or "").lower())
    return frozenset(_stem(w) for w in words if w not in _STOPWORDS and len(w) > 2)


def _containment(a: frozenset, b: frozenset) -> float:
    """Overlap relative to the SMALLER title — headlines paraphrase, so
    union-Jaccard under-scores true duplicates of different lengths."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def dedupe(events: list[dict]) -> list[dict]:
    """Collapse near-duplicate titles; keep the highest-scored instance.

    Input should be sorted by relevance_score desc (survivors are the first
    seen). Survivor gains `duplicate_count` when it absorbed others.
    """
    survivors: list[dict] = []
    token_cache: list[frozenset] = []
    for e in events:
        t = _tokens(e.get("title", ""))
        matched = None
        for i, st in enumerate(token_cache):
            if _containment(t, st) >= _CONTAINMENT_THRESHOLD:
                matched = i
                break
        if matched is None:
            survivors.append(dict(e))
            token_cache.append(t)
        else:
            survivors[matched]["duplicate_count"] = survivors[matched].get("duplicate_count", 1) + 1
    return survivors


def apply_decay(events: list[dict], as_of: date) -> list[dict]:
    """Fade relevance_score once effective_start has passed (5%/day, floor 40%)."""
    out = []
    for e in events:
        e = dict(e)
        start = e.get("effective_start")
        try:
            days_past = (as_of - date.fromisoformat(start)).days if start else 0
        except (TypeError, ValueError):
            days_past = 0
        if days_past > 0:
            factor = max(_DECAY_FLOOR, 1.0 - _DECAY_PER_DAY * days_past)
            e["decayed_score"] = round(e.get("relevance_score", 0) * factor)
            e["decay_days"] = days_past
        else:
            e["decayed_score"] = e.get("relevance_score", 0)
        out.append(e)
    out.sort(key=lambda x: -x["decayed_score"])
    return out


def cluster_themes(events: list[dict]) -> list[dict]:
    """(event_type, direction) theme groups, sorted by best decayed score."""
    groups: dict[tuple, dict] = {}
    for e in events:
        key = (e.get("event_type", "other"), e.get("direction", "unclear"))
        g = groups.setdefault(key, {"event_type": key[0], "direction": key[1],
                                    "count": 0, "top_title": None, "top_score": -1})
        g["count"] += e.get("duplicate_count", 1)
        score = e.get("decayed_score", e.get("relevance_score", 0))
        if score > g["top_score"]:
            g["top_score"] = score
            g["top_title"] = e.get("title")
    return sorted(groups.values(), key=lambda g: -g["top_score"])


def top_directional(events: list[dict], direction: str) -> Optional[dict]:
    """Highest-scored (post-dedup/decay) event with the given direction."""
    for e in events:  # already sorted by decayed score
        if e.get("direction") == direction:
            return e
    return None

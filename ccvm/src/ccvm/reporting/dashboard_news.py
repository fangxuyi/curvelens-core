"""Build dashboard news highlights from validated specialist citations."""
from __future__ import annotations

from typing import Any, Iterable


def _news_ids(value: dict[str, Any]) -> list[str]:
    return [
        item for item in value.get("evidence_ids", [])
        if isinstance(item, str) and item.startswith("news:")
    ]


def _nested_evidence_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(_news_ids(value))
        for nested in value.values():
            found.update(_nested_evidence_ids(nested))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for nested in value:
            found.update(_nested_evidence_ids(nested))
        return found
    return set()


def build_validated_news(
    analysis: dict[str, Any], role_packets: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return ranked stories that specialists actually cited in news findings.

    Packets provide article metadata; validated specialist responses decide which
    routed articles count as processed news. Ranking favors citations that reach
    the synthesis top views, followed by cross-specialist use and finding count.
    """
    metadata: dict[str, dict[str, Any]] = {}
    for packet in role_packets:
        for article in packet.get("relevant_news", []):
            article_id = article.get("article_id")
            if isinstance(article_id, str):
                metadata.setdefault(article_id, article)

    stories: dict[str, dict[str, Any]] = {}
    specialist_analyses = analysis.get("specialist_analyses") or {}
    for role, response in specialist_analyses.items():
        for finding in response.get("news_findings", []):
            for article_id in _news_ids(finding):
                story = stories.setdefault(article_id, {
                    "article_id": article_id,
                    "roles": set(),
                    "findings": [],
                    "market_comparisons": [],
                    "top_view_titles": [],
                    "top_view_relationships": [],
                })
                story["roles"].add(role)
                claim = str(finding.get("claim") or "").strip()
                if claim and claim not in story["findings"]:
                    story["findings"].append(claim)

    # A comparison is useful only after a specialist promoted that article into
    # a validated news finding. This prevents keyword routing from becoming news.
    for response in specialist_analyses.values():
        for comparison in response.get("data_news_comparison", []):
            claim = str(comparison.get("claim") or "").strip()
            for article_id in _news_ids(comparison):
                story = stories.get(article_id)
                if story is not None and claim and claim not in story["market_comparisons"]:
                    story["market_comparisons"].append(claim)

    top_views = (analysis.get("synthesis") or {}).get("top_views") or []
    for view in top_views:
        cited = _nested_evidence_ids(view)
        for article_id in cited:
            story = stories.get(article_id)
            if story is None:
                continue
            title = str(view.get("title") or "").strip()
            relationship = str(view.get("evidence_relationship") or "").strip()
            if title and title not in story["top_view_titles"]:
                story["top_view_titles"].append(title)
            if relationship and relationship not in story["top_view_relationships"]:
                story["top_view_relationships"].append(relationship)

    ranked = []
    for article_id, story in stories.items():
        article = metadata.get(article_id, {})
        roles = sorted(story["roles"])
        top_view_count = len(story["top_view_titles"])
        story.update({
            "title": article.get("title") or article_id,
            "published_at": article.get("published_at"),
            "source_name": article.get("source_name"),
            "url": article.get("url"),
            "summary_text": article.get("summary_text"),
            "roles": roles,
            "score": top_view_count * 100 + len(roles) * 10 + len(story["findings"]),
        })
        ranked.append(story)

    return sorted(
        ranked,
        key=lambda item: (
            item["score"], str(item.get("published_at") or ""), item["article_id"]
        ),
        reverse=True,
    )

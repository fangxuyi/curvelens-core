"""Build bounded, cited evidence packets for specialist analysis agents."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ccvm.reference.product import Product

_REPO_ROOT = Path(__file__).resolve().parents[4]
PACKET_SCHEMA_VERSION = 4


def load_articles(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    value = json.loads(path.read_text())
    return value if isinstance(value, list) else []


def _article_id(article: dict[str, Any]) -> str:
    raw = str(article.get("url") or article.get("title") or article)
    return "news:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _normalized_article(article: dict[str, Any]) -> dict[str, Any]:
    return {
        key: article.get(key)
        for key in ("title", "text", "url", "published_at", "source_key", "source_name")
    }


def _route_articles(articles: list[dict[str, Any]], keywords: tuple[str, ...]) -> list[dict]:
    routed = []
    seen: set[str] = set()
    for article in articles:
        text = f"{article.get('title', '')} {article.get('text', '')}".lower()
        matches = sorted({word for word in keywords if word in text})
        if not matches:
            continue
        article_id = _article_id(article)
        if article_id in seen:
            continue
        seen.add(article_id)
        routed.append({
            "article_id": article_id,
            "title": article.get("title"),
            "published_at": article.get("published_at"),
            "source_name": article.get("source_name"),
            "url": article.get("url"),
            "summary_text": article.get("text"),
            "matched_keywords": matches,
        })
    return sorted(routed, key=lambda x: (x.get("published_at") or "", x["article_id"]), reverse=True)


def _response_template(role_key: str, packet_id: str) -> dict[str, Any]:
    return {
        "packet_id": packet_id,
        "role": role_key,
        "status": "complete|limited|blocked",
        "data_quality_assessment": "",
        "key_metrics": [],
        "data_findings": [],
        "news_findings": [],
        "data_news_comparison": [],
        "required_check_results": [],
        "forward_view": {
            "horizon": "",
            "bias": "",
            "thesis": "",
            "confirmations": [],
            "invalidations": [],
        },
        "open_questions": [],
        "evidence_ids": [],
    }


def build_analysis_packets(
    *, product: Product, trade_date: str, report: dict[str, Any],
    quality: dict[str, Any], articles: list[dict[str, Any]], output_dir: Path,
) -> dict[str, Any]:
    """Write one independent packet per configured role plus a coordinator manifest."""
    sections = report.get("sections", {})
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, dict[str, Any]] = {}
    role_packets: dict[str, str] = {}
    role_templates: dict[str, str] = {}
    role_responses: dict[str, str] = {}
    role_packet_hashes: dict[str, str] = {}

    configured_sections = {
        key for role in product.analysis_roles for key in role.section_keys
    }
    for key in set(sections) | configured_sections:
        value = sections.get(key, {"status": "unavailable"})
        evidence_id = f"feature:{key}:{trade_date}"
        evidence[evidence_id] = {"kind": "computed_feature", "section": key, "value": value}
    for article in articles:
        aid = _article_id(article)
        evidence[aid] = {
            "kind": "news", "title": article.get("title"),
            "published_at": article.get("published_at"),
            "source_name": article.get("source_name"), "url": article.get("url"),
        }
    knowledge_sources = []
    knowledge_dir = _REPO_ROOT / "knowledge" / product.knowledge_pack
    if knowledge_dir.exists():
        for path in sorted(p for p in knowledge_dir.rglob("*") if p.is_file()):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rel = path.relative_to(_REPO_ROOT).as_posix()
            evidence_id = f"knowledge:{rel}:{digest[:12]}"
            item = {"evidence_id": evidence_id, "path": str(path), "sha256": digest}
            knowledge_sources.append(item)
            evidence[evidence_id] = {"kind": "knowledge", **item}

    fingerprint = json.dumps({
        "product": product.key, "trade_date": trade_date,
        "sections": sections, "quality": quality,
        "articles": sorted(
            (_normalized_article(a) for a in articles),
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        ),
        "knowledge": sorted(item["evidence_id"] for item in knowledge_sources),
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "analysis_contract": [
            {
                "key": role.key, "display_name": role.display_name,
                "mandate": role.mandate, "section_keys": role.section_keys,
                "news_keywords": role.news_keywords,
                "required_checks": role.required_checks,
                "report_requirements": role.report_requirements,
                "minimum_key_metrics": role.minimum_key_metrics,
            }
            for role in product.analysis_roles
        ],
        "quality_policy": {
            "blocking_sections": product.analysis_blocking_sections,
            "retryable_empty_sections": product.analysis_retryable_empty_sections,
            "max_quality_attempts": product.analysis_max_quality_attempts,
        },
    }, sort_keys=True, default=str).encode()
    packet_id = hashlib.sha256(fingerprint).hexdigest()

    for role in product.analysis_roles:
        selected = {
            key: {
                "evidence_id": f"feature:{key}:{trade_date}",
                "value": sections.get(key, {"status": "unavailable"}),
            }
            for key in role.section_keys
        }
        news = _route_articles(articles, role.news_keywords)
        packet = {
            "schema_version": PACKET_SCHEMA_VERSION,
            "packet_id": packet_id,
            "product": product.display_name,
            "trade_date": trade_date,
            "role": role.key,
            "display_name": role.display_name,
            "mandate": role.mandate,
            "quality": quality,
            "computed_sections": selected,
            "relevant_news": news,
            "knowledge_sources": knowledge_sources,
            "required_checks": list(role.required_checks),
            "report_requirements": list(role.report_requirements),
            "minimum_key_metrics": role.minimum_key_metrics,
            "analysis_contract": {
                "sequence": [
                    "assess data quality and disclose limitations",
                    "state what computed market data says",
                    "state what relevant news says and assess source/date relevance",
                    "classify every news finding as relevant, context_only, or rejected",
                    "compare agreement, contradiction, or missing linkage",
                    "form a forward view with confirmations and invalidations",
                ],
                "numeric_rule": (
                    "Lead with exact current values and changes. Return at least "
                    f"{role.minimum_key_metrics} key_metrics, following report_requirements. "
                    "Each value must contain a number and unit; comparison must state the date, "
                    "prior value, percentile, or named benchmark when available."
                ),
                "history_rule": (
                    "Use measured history_context when mature. When local history is young, compare "
                    "with applicable knowledge-pack or external-proxy benchmarks, label the source "
                    "and non-equivalence, and mention the young history once rather than repeating it."
                ),
                "language_rule": (
                    "Use short plain-English sentences. Define any unavoidable market term on first use. "
                    "Do not replace numbers with abstract labels or unsupported opinions."
                ),
                "citation_rule": "Every factual or numerical claim must cite an evidence_id from this packet.",
                "epistemic_rule": "Label verified observations, interpretations, and open questions separately.",
                "finding_schema": {
                    "data_findings": {"claim": "text", "evidence_ids": ["feature:..."]},
                    "news_findings": {
                        "claim": "text",
                        "relevance": "relevant|context_only|rejected",
                        "evidence_ids": ["news:..."],
                    },
                    "data_news_comparison": {"claim": "text", "evidence_ids": ["feature:...", "news:..."]},
                },
                "key_metric_schema": {
                    "label": "short market measure",
                    "value": "number with unit",
                    "comparison": "dated prior value or explicitly named benchmark",
                    "plain_english_meaning": "one short sentence",
                    "evidence_ids": ["feature:..."],
                },
                "required_check_schema": {
                    "instruction": "Return one item per required_checks entry, preserving the exact text and order.",
                    "item": {
                        "check": "exact required_checks text",
                        "status": "pass|concern|not_applicable",
                        "evidence_ids": ["allowed evidence ID"],
                    },
                },
            },
        }
        packet_path = output_dir / f"{role.key}.packet.json"
        template_path = output_dir / f"{role.key}.template.json"
        response_path = output_dir / f"{role.key}.response.json"
        packet_path.write_text(json.dumps(packet, indent=2, default=str))
        role_packet_hashes[role.key] = hashlib.sha256(packet_path.read_bytes()).hexdigest()
        template_path.write_text(json.dumps(_response_template(role.key, packet_id), indent=2))
        response_path.unlink(missing_ok=True)
        role_packets[role.key] = str(packet_path)
        role_templates[role.key] = str(template_path)
        role_responses[role.key] = str(response_path)

    manifest = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "packet_id": packet_id,
        "product": product.key,
        "trade_date": trade_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roles": [role.key for role in product.analysis_roles],
        "role_packets": role_packets,
        "role_packet_hashes": role_packet_hashes,
        "role_response_templates": role_templates,
        "role_response_paths": role_responses,
        "knowledge_pack": product.knowledge_pack,
        "evidence_registry": evidence,
        "synthesis_contract": {
            "wait_for_all_roles": True,
            "required_sections": [role.key for role in product.analysis_roles],
            "focus": "Forward-looking risks, cross-section agreements, tensions, confirmations, and invalidations.",
            "reporting": {
                "top_views": (
                    "Rank exactly three distinct market views by decision relevance. Each view must state "
                    "the condition, why it matters, 2-3 exact key metrics, supporting evidence, any "
                    "conflicting evidence, the best-supported driver explanation (or explicitly say the "
                    "driver is unexplained), what to watch next, horizon, confidence, and whether it is "
                    "cross-supported, conflicting, or a single-desk observation. Across the three views, "
                    "cover every configured specialist role."
                ),
                "top_view_schema": {
                    "rank": "1|2|3",
                    "title": "short concrete market condition",
                    "plain_english_view": "what is happening and why it matters",
                    "horizon": "time window",
                    "confidence": "high|medium|low",
                    "evidence_relationship": "cross_supported|conflicting|single_desk",
                    "specialist_roles": ["configured role key"],
                    "key_metrics": ["copy 2-3 complete specialist key_metric objects exactly"],
                    "supporting_evidence": [{"claim": "reason", "evidence_ids": ["allowed ID"]}],
                    "conflicting_evidence": [{"claim": "contrary evidence", "evidence_ids": ["allowed ID"]}],
                    "driver_analysis": {
                        "status": "supported|partially_supported|conflicting|unexplained",
                        "explanation": "plain-English causal interpretation without overstating attribution",
                        "evidence_ids": ["validated specialist evidence ID"],
                    },
                    "what_to_watch": ["specific confirmation or invalidation with a level or event"],
                },
                "market_snapshot_items": "6 to 10 exact values drawn from specialist key_metrics",
                "plain_english": (
                    "Write for an informed reader who is not an options specialist. Use short sentences, "
                    "define risk reversal and butterfly if used, and avoid desk jargon such as internals, "
                    "macro prior, carry headwind, or conviction unless immediately explained."
                ),
                "limitations": "Consolidate duplicate limitations; keep the delivery-facing list to the material items.",
            },
            "do_not": [
                "invent missing evidence", "present settlement analytics as executable prices",
                "turn an invalid diagnostic into a probability",
                "claim that news caused a move when evidence only shows timing or correlation",
            ],
        },
    }
    synthesis_template = {
        "packet_id": packet_id,
        "status": "complete|limited|blocked",
        "headline": "",
        "executive_summary": "",
        "plain_english_summary": "",
        "top_views": [],
        "market_snapshot": [],
        "overall_forward_view": {"horizon": "", "bias": "", "thesis": ""},
        "cross_role_agreements": [],
        "cross_role_tensions": [],
        "key_risks": [],
        "confirmations": [],
        "invalidations": [],
        "data_limitations": [],
        "evidence_ids": [],
    }
    synthesis_template_path = output_dir / "synthesis.template.json"
    synthesis_response_path = output_dir / "synthesis.response.json"
    synthesis_template_path.write_text(json.dumps(synthesis_template, indent=2))
    synthesis_response_path.unlink(missing_ok=True)
    manifest["synthesis_response_template"] = str(synthesis_template_path)
    manifest["synthesis_response_path"] = str(synthesis_response_path)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    return manifest

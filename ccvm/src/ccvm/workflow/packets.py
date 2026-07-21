"""Build bounded, cited evidence packets for specialist analysis agents."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ccvm.reference.product import Product


def load_articles(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    value = json.loads(path.read_text())
    return value if isinstance(value, list) else []


def _article_id(article: dict[str, Any]) -> str:
    raw = str(article.get("url") or article.get("title") or article)
    return "news:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


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
        "data_findings": [],
        "news_findings": [],
        "data_news_comparison": [],
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

    fingerprint = json.dumps({
        "product": product.key, "trade_date": trade_date,
        "sections": sections, "quality": quality,
        "articles": sorted(_article_id(a) for a in articles),
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
            "packet_id": packet_id,
            "product": product.display_name,
            "trade_date": trade_date,
            "role": role.key,
            "display_name": role.display_name,
            "mandate": role.mandate,
            "quality": quality,
            "computed_sections": selected,
            "relevant_news": news,
            "required_checks": list(role.required_checks),
            "analysis_contract": {
                "sequence": [
                    "assess data quality and disclose limitations",
                    "state what computed market data says",
                    "state what relevant news says and assess source/date relevance",
                    "compare agreement, contradiction, or missing linkage",
                    "form a forward view with confirmations and invalidations",
                ],
                "citation_rule": "Every factual or numerical claim must cite an evidence_id from this packet.",
                "epistemic_rule": "Label verified observations, interpretations, and open questions separately.",
                "finding_schema": {
                    "data_findings": {"claim": "text", "evidence_ids": ["feature:..."]},
                    "news_findings": {"claim": "text", "evidence_ids": ["news:..."]},
                    "data_news_comparison": {"claim": "text", "evidence_ids": ["feature:...", "news:..."]},
                },
            },
        }
        packet_path = output_dir / f"{role.key}.packet.json"
        template_path = output_dir / f"{role.key}.response.json"
        packet_path.write_text(json.dumps(packet, indent=2, default=str))
        template_path.write_text(json.dumps(_response_template(role.key, packet_id), indent=2))
        role_packets[role.key] = str(packet_path)
        role_templates[role.key] = str(template_path)

    manifest = {
        "packet_id": packet_id,
        "product": product.key,
        "trade_date": trade_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roles": [role.key for role in product.analysis_roles],
        "role_packets": role_packets,
        "role_response_templates": role_templates,
        "evidence_registry": evidence,
        "synthesis_contract": {
            "wait_for_all_roles": True,
            "required_sections": [role.key for role in product.analysis_roles],
            "focus": "Forward-looking risks, cross-section agreements, tensions, confirmations, and invalidations.",
            "do_not": [
                "invent missing evidence", "present settlement analytics as executable prices",
                "turn an invalid diagnostic into a probability",
            ],
        },
    }
    synthesis_template = {
        "packet_id": packet_id,
        "status": "complete|limited|blocked",
        "headline": "",
        "executive_summary": "",
        "overall_forward_view": {"horizon": "", "bias": "", "thesis": ""},
        "cross_role_agreements": [],
        "cross_role_tensions": [],
        "key_risks": [],
        "confirmations": [],
        "invalidations": [],
        "data_limitations": [],
        "evidence_ids": [],
    }
    synthesis_path = output_dir / "synthesis.response.json"
    synthesis_path.write_text(json.dumps(synthesis_template, indent=2))
    manifest["synthesis_response_template"] = str(synthesis_path)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    return manifest

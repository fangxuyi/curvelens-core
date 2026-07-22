"""Validate agent-framework outputs and render the daily analysis report."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .packets import PACKET_SCHEMA_VERSION

_STATUSES = {"complete", "limited", "blocked"}


class AnalysisValidationError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AnalysisValidationError(f"missing response: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise AnalysisValidationError(f"response must be an object: {path}")
    return value


def _check_ids(ids: Any, allowed: set[str], label: str) -> list[str]:
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise AnalysisValidationError(f"{label}.evidence_ids must be a list of strings")
    unknown = sorted(set(ids) - allowed)
    if unknown:
        raise AnalysisValidationError(f"{label} cites unknown evidence: {unknown}")
    return ids


def _check_findings(response: dict[str, Any], allowed: set[str], role: str) -> None:
    for field in ("data_findings", "news_findings", "data_news_comparison"):
        findings = response.get(field)
        if not isinstance(findings, list):
            raise AnalysisValidationError(f"{role}.{field} must be a list")
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict) or not str(finding.get("claim", "")).strip():
                raise AnalysisValidationError(
                    f"{role}.{field}[{index}] must contain a claim"
                )
            _check_ids(finding.get("evidence_ids"), allowed, f"{role}.{field}[{index}]")


def _check_key_metrics(
    metrics: Any, allowed: set[str], label: str, minimum: int,
) -> list[dict[str, Any]]:
    if not isinstance(metrics, list) or len(metrics) < minimum:
        raise AnalysisValidationError(f"{label} requires at least {minimum} key_metrics")
    for index, metric in enumerate(metrics):
        item_label = f"{label}.key_metrics[{index}]"
        if not isinstance(metric, dict):
            raise AnalysisValidationError(f"{item_label} must be an object")
        for field in ("label", "value", "comparison", "plain_english_meaning"):
            if not str(metric.get(field, "")).strip():
                raise AnalysisValidationError(f"{item_label}.{field} is required")
        if not re.search(r"\d", str(metric["value"])):
            raise AnalysisValidationError(f"{item_label}.value must contain a number")
        _check_ids(metric.get("evidence_ids"), allowed, item_label)
    return metrics


def _check_top_views(
    views: Any, responses: dict[str, dict[str, Any]], allowed: set[str],
) -> set[str]:
    if not isinstance(views, list) or len(views) != 3:
        raise AnalysisValidationError("synthesis must contain exactly three top_views")
    if [item.get("rank") if isinstance(item, dict) else None for item in views] != [1, 2, 3]:
        raise AnalysisValidationError("synthesis top_views must be ranked 1, 2, 3")
    valid_roles = set(responses)
    covered_roles: set[str] = set()
    cited_ids: set[str] = set()
    specialist_metrics = {
        (
            str(metric.get("label", "")), str(metric.get("value", "")),
            str(metric.get("comparison", "")), str(metric.get("plain_english_meaning", "")),
        )
        for response in responses.values() for metric in response.get("key_metrics", [])
    }
    evidence_roles: dict[str, set[str]] = {}
    for role, response in responses.items():
        for evidence_id in response.get("evidence_ids", []):
            evidence_roles.setdefault(evidence_id, set()).add(role)
    for index, view in enumerate(views):
        label = f"synthesis.top_views[{index}]"
        if not isinstance(view, dict):
            raise AnalysisValidationError(f"{label} must be an object")
        for field in ("title", "plain_english_view", "horizon", "confidence"):
            if not str(view.get(field, "")).strip():
                raise AnalysisValidationError(f"{label}.{field} is required")
        if view.get("confidence") not in {"high", "medium", "low"}:
            raise AnalysisValidationError(f"{label}.confidence must be high, medium, or low")
        relationship = view.get("evidence_relationship")
        if relationship not in {"cross_supported", "conflicting", "single_desk"}:
            raise AnalysisValidationError(f"{label}.evidence_relationship is invalid")
        roles = view.get("specialist_roles")
        if not isinstance(roles, list) or not roles or set(roles) - valid_roles:
            raise AnalysisValidationError(f"{label}.specialist_roles must name configured roles")
        if relationship in {"cross_supported", "conflicting"} and len(set(roles)) < 2:
            raise AnalysisValidationError(f"{label} requires at least two specialist roles")
        covered_roles.update(roles)
        metrics = _check_key_metrics(view.get("key_metrics"), allowed, label, 2)
        view_ids: set[str] = set()
        for metric in metrics:
            identity = (
                str(metric.get("label", "")), str(metric.get("value", "")),
                str(metric.get("comparison", "")), str(metric.get("plain_english_meaning", "")),
            )
            if identity not in specialist_metrics:
                raise AnalysisValidationError(
                    f"{label}.key_metrics must copy validated specialist metrics exactly"
                )
            view_ids.update(metric["evidence_ids"])
        for field in ("supporting_evidence", "conflicting_evidence"):
            claims = view.get(field)
            if not isinstance(claims, list):
                raise AnalysisValidationError(f"{label}.{field} must be a list")
            if field == "supporting_evidence" and not claims:
                raise AnalysisValidationError(f"{label}.supporting_evidence cannot be empty")
            if relationship == "conflicting" and field == "conflicting_evidence" and not claims:
                raise AnalysisValidationError(f"{label}.conflicting_evidence cannot be empty")
            for claim_index, claim in enumerate(claims):
                claim_label = f"{label}.{field}[{claim_index}]"
                if not isinstance(claim, dict) or not str(claim.get("claim", "")).strip():
                    raise AnalysisValidationError(f"{claim_label} must contain a claim")
                ids = _check_ids(claim.get("evidence_ids"), allowed, claim_label)
                if not ids:
                    raise AnalysisValidationError(f"{claim_label} must cite evidence")
                view_ids.update(ids)
        roles_from_evidence = set().union(*(evidence_roles.get(item, set()) for item in view_ids))
        if not set(roles).issubset(roles_from_evidence):
            raise AnalysisValidationError(f"{label}.specialist_roles are not supported by cited evidence")
        cited_ids.update(view_ids)
    if covered_roles != valid_roles:
        raise AnalysisValidationError("synthesis top_views must collectively cover every specialist role")
    return cited_ids


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = _read(manifest_path)
    if manifest.get("schema_version") != PACKET_SCHEMA_VERSION:
        raise AnalysisValidationError("unsupported analysis packet schema")
    roles = manifest.get("roles")
    if not isinstance(roles, list) or not roles \
            or not all(isinstance(role, str) and role for role in roles) \
            or len(roles) != len(set(roles)):
        raise AnalysisValidationError("manifest must contain unique, non-empty roles")
    for mapping in ("role_packets", "role_packet_hashes", "role_response_paths"):
        value = manifest.get(mapping)
        if not isinstance(value, dict) or set(value) != set(roles):
            raise AnalysisValidationError(f"manifest {mapping} must cover every role")
    return manifest


def validate_role_response(
    manifest_path: Path, role: str, response_path: Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    if role not in manifest["roles"]:
        raise AnalysisValidationError(f"unknown role: {role}")
    packet = _read(Path(manifest["role_packets"][role]))
    packet_hash = hashlib.sha256(
        Path(manifest["role_packets"][role]).read_bytes()
    ).hexdigest()
    if packet_hash != manifest["role_packet_hashes"][role]:
        raise AnalysisValidationError(f"{role} packet content hash does not match manifest")
    response = _read(response_path or Path(manifest["role_response_paths"][role]))
    packet_id = manifest.get("packet_id")
    if response.get("packet_id") != packet_id or response.get("role") != role:
        raise AnalysisValidationError(f"{role} response does not match this packet")
    if response.get("status") not in _STATUSES:
        raise AnalysisValidationError(f"{role} has invalid or placeholder status")
    if not str(response.get("data_quality_assessment", "")).strip():
        raise AnalysisValidationError(f"{role} must assess data quality")
    allowed = {
        item["evidence_id"] for item in packet.get("computed_sections", {}).values()
    } | {item["article_id"] for item in packet.get("relevant_news", [])} \
      | {item["evidence_id"] for item in packet.get("knowledge_sources", [])}
    _check_ids(response.get("evidence_ids"), allowed, role)
    _check_findings(response, allowed, role)
    metrics = _check_key_metrics(
        response.get("key_metrics"), allowed, role,
        0 if response["status"] == "blocked" else int(packet.get("minimum_key_metrics", 1)),
    )
    expected_checks = list(packet.get("required_checks", []))
    results = response.get("required_check_results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results) \
            or [item.get("check") for item in results] != expected_checks:
        raise AnalysisValidationError(f"{role} must answer every required check in order")
    for index, item in enumerate(results):
        if item.get("status") not in {"pass", "concern", "not_applicable"}:
            raise AnalysisValidationError(f"{role}.required_check_results[{index}] has invalid status")
        _check_ids(item.get("evidence_ids"), allowed, f"{role}.required_check_results[{index}]")
    cited_in_findings = {
        evidence_id
        for field in ("data_findings", "news_findings", "data_news_comparison")
        for finding in response[field]
        for evidence_id in finding["evidence_ids"]
    } | {
        evidence_id for item in results for evidence_id in item["evidence_ids"]
    } | {
        evidence_id for item in metrics for evidence_id in item["evidence_ids"]
    }
    if not cited_in_findings.issubset(set(response["evidence_ids"])):
        raise AnalysisValidationError(
            f"{role}.evidence_ids must include every finding and required-check citation"
        )
    forward_view = response.get("forward_view")
    if not isinstance(forward_view, dict):
        raise AnalysisValidationError(f"{role}.forward_view must be an object")
    for field in ("confirmations", "invalidations"):
        if not isinstance(forward_view.get(field), list):
            raise AnalysisValidationError(f"{role}.forward_view.{field} must be a list")
    if response["status"] == "blocked":
        if not response.get("open_questions"):
            raise AnalysisValidationError(f"{role} blocked response must explain the blocker")
    elif not str(forward_view.get("thesis", "")).strip():
        raise AnalysisValidationError(f"{role} must provide a forward-view thesis")
    elif not str(forward_view.get("horizon", "")).strip() or not str(
        forward_view.get("bias", "")
    ).strip():
        raise AnalysisValidationError(f"{role} forward view requires horizon and bias")
    return response


def validate_synthesis_response(
    manifest_path: Path, responses: dict[str, dict[str, Any]],
    response_path: Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    if set(responses) != set(manifest["roles"]):
        raise AnalysisValidationError("synthesis requires every configured role")
    synthesis = _read(response_path or Path(manifest["synthesis_response_path"]))
    if synthesis.get("packet_id") != manifest.get("packet_id"):
        raise AnalysisValidationError("synthesis response does not match this packet")
    if synthesis.get("status") not in _STATUSES:
        raise AnalysisValidationError("synthesis has invalid or placeholder status")
    if any(item.get("status") == "blocked" for item in responses.values()) \
            and synthesis.get("status") == "complete":
        raise AnalysisValidationError("synthesis cannot be complete when a required role is blocked")
    if any(item.get("status") == "limited" for item in responses.values()) \
            and synthesis.get("status") == "complete":
        raise AnalysisValidationError("synthesis cannot be complete when a required role is limited")
    if not str(synthesis.get("headline", "")).strip() or not str(
        synthesis.get("executive_summary", "")
    ).strip():
        raise AnalysisValidationError("synthesis requires a headline and executive summary")
    plain_summary = str(synthesis.get("plain_english_summary", "")).strip()
    if not plain_summary:
        raise AnalysisValidationError("synthesis requires a plain_english_summary")
    if re.search(r"\[(?:feature|knowledge|news):", plain_summary):
        raise AnalysisValidationError(
            "synthesis plain_english_summary must not embed evidence IDs"
        )
    view = synthesis.get("overall_forward_view")
    if not isinstance(view, dict):
        raise AnalysisValidationError("synthesis overall_forward_view must be an object")
    for field in (
        "cross_role_agreements", "cross_role_tensions", "key_risks",
        "confirmations", "invalidations", "data_limitations",
    ):
        if not isinstance(synthesis.get(field), list):
            raise AnalysisValidationError(f"synthesis {field} must be a list")
    if synthesis["status"] != "blocked":
        for field in ("horizon", "bias", "thesis"):
            if not str(view.get(field, "")).strip():
                raise AnalysisValidationError(f"synthesis forward view requires {field}")
    elif not synthesis["data_limitations"]:
        raise AnalysisValidationError("blocked synthesis must identify data limitations")
    if any(item.get("status") in {"limited", "blocked"} for item in responses.values()) \
            and not synthesis["data_limitations"]:
        raise AnalysisValidationError("synthesis must preserve specialist limitations")
    allowed = set().union(*(set(item.get("evidence_ids", [])) for item in responses.values()))
    top_view_ids = (
        set() if synthesis["status"] == "blocked"
        else _check_top_views(synthesis.get("top_views"), responses, allowed)
    )
    snapshot = _check_key_metrics(
        synthesis.get("market_snapshot"), allowed, "synthesis",
        0 if synthesis["status"] == "blocked" else 6,
    )
    _check_ids(synthesis.get("evidence_ids"), allowed, "synthesis")
    if synthesis["status"] != "blocked" and not synthesis["evidence_ids"]:
        raise AnalysisValidationError("synthesis requires cited evidence")
    snapshot_ids = {
        evidence_id for item in snapshot for evidence_id in item["evidence_ids"]
    }
    if not snapshot_ids.issubset(set(synthesis["evidence_ids"])):
        raise AnalysisValidationError(
            "synthesis.evidence_ids must include every market-snapshot citation"
        )
    if not top_view_ids.issubset(set(synthesis["evidence_ids"])):
        raise AnalysisValidationError(
            "synthesis.evidence_ids must include every top-view citation"
        )
    return synthesis


def validate_and_render(manifest_path: Path, output_dir: Path) -> tuple[Path, Path]:
    manifest = load_manifest(manifest_path)
    packet_id = manifest.get("packet_id")
    responses = {
        role: validate_role_response(manifest_path, role)
        for role in manifest["roles"]
    }
    synthesis = validate_synthesis_response(manifest_path, responses)

    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "packet_id": packet_id,
        "product": manifest.get("product"),
        "trade_date": manifest.get("trade_date"),
        "specialist_analyses": responses,
        "synthesis": synthesis,
        "status": synthesis["status"],
        "workflow_mode": "agent_orchestrated",
        "delivery_approved": False,
    }
    json_path = output_dir / "analysis.json"
    md_path = output_dir / "analysis.md"
    json_path.write_text(json.dumps(result, indent=2))
    md_path.write_text(_render_markdown(result))
    return json_path, md_path


def _render_markdown(result: dict[str, Any]) -> str:
    synthesis = result["synthesis"]
    lines = [
        f"# {result['product'].upper()} Forward Analysis — {result['trade_date']}",
        "", "> Agent-orchestrated daily analysis — automatic delivery is not enabled.", "",
        f"## {synthesis.get('headline') or 'Executive Summary'}", "",
        synthesis.get("plain_english_summary") or synthesis.get("executive_summary", ""), "",
        "## Top three market views", "",
    ]
    for view in synthesis.get("top_views", []):
        relationship = str(view.get("evidence_relationship", "")).replace("_", " ")
        lines.extend([
            f"### {view.get('rank')}. {view.get('title', '')}", "",
            f"- Evidence relationship: {relationship}",
            f"- Confidence: {view.get('confidence', '')}",
            f"- Horizon: {view.get('horizon', '')}",
            f"- View: {view.get('plain_english_view', '')}",
            "- Key numbers:",
        ])
        for metric in view.get("key_metrics", []):
            lines.append(
                f"  - {metric.get('label', '')}: {metric.get('value', '')} "
                f"({metric.get('comparison', '')})"
            )
        lines.append("- Supporting evidence:")
        lines.extend(
            f"  - {item.get('claim', '')}" for item in view.get("supporting_evidence", [])
        )
        lines.append("- Conflicting evidence:")
        conflicts = view.get("conflicting_evidence", [])
        lines.extend(f"  - {item.get('claim', '')}" for item in conflicts)
        if not conflicts:
            lines.append("  - None identified.")
        lines.append("")
    lines.extend(["## Market snapshot", ""])
    for item in synthesis.get("market_snapshot", []):
        comparison = f" ({item['comparison']})" if item.get("comparison") else ""
        lines.append(
            f"- **{item.get('label', '')}: {item.get('value', '')}**{comparison} — "
            f"{item.get('plain_english_meaning', '')}"
        )
    lines.append("")
    overall = synthesis.get("overall_forward_view", {})
    lines.extend([
        "### Overall forward view", "",
        f"- Horizon: {overall.get('horizon', '')}",
        f"- Bias: {overall.get('bias', '')}",
        f"- Thesis: {overall.get('thesis', '')}", "",
    ])
    for heading, key in (
        ("Cross-role agreements", "cross_role_agreements"),
        ("Cross-role tensions", "cross_role_tensions"),
        ("Key risks", "key_risks"),
        ("Confirmations", "confirmations"),
        ("Invalidations", "invalidations"),
        ("Data limitations", "data_limitations"),
    ):
        values = synthesis.get(key, [])
        lines.extend([f"### {heading}", ""])
        lines.extend(
            f"- {item if isinstance(item, str) else json.dumps(item)}" for item in values
        )
        if not values:
            lines.append("- None identified.")
        lines.append("")
    for role, response in result["specialist_analyses"].items():
        title = role.replace("_", " ").title()
        lines.extend([f"## {title}", "", f"Status: {response['status']}", ""])
        lines.extend(["### Key numbers", ""])
        for item in response.get("key_metrics", []):
            comparison = f" ({item['comparison']})" if item.get("comparison") else ""
            lines.append(
                f"- **{item.get('label', '')}: {item.get('value', '')}**{comparison} — "
                f"{item.get('plain_english_meaning', '')}"
            )
        lines.append("")
        for heading, key in (
            ("Data quality", "data_quality_assessment"),
            ("What the data says", "data_findings"),
            ("What the news says", "news_findings"),
            ("Data versus news", "data_news_comparison"),
            ("Open questions", "open_questions"),
        ):
            value = response.get(key)
            if value:
                lines.extend([f"### {heading}", ""])
                if isinstance(value, list):
                    lines.extend(
                        f"- {item if isinstance(item, str) else item.get('claim', json.dumps(item))}"
                        for item in value
                    )
                else:
                    lines.append(str(value))
                lines.append("")
        view = response.get("forward_view", {})
        lines.extend(["### Forward view", "", str(view.get("thesis", "")), ""])
    lines.extend(["## Evidence IDs", "", ", ".join(synthesis.get("evidence_ids", [])), ""])
    return "\n".join(lines)

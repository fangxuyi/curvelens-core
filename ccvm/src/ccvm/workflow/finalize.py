"""Validate agent-framework outputs and render the shadow analysis report."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    if not isinstance(ids, list):
        raise AnalysisValidationError(f"{label}.evidence_ids must be a list")
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


def validate_and_render(manifest_path: Path, output_dir: Path) -> tuple[Path, Path]:
    manifest = _read(manifest_path)
    packet_id = manifest.get("packet_id")
    registry = set(manifest.get("evidence_registry", {}))
    responses: dict[str, dict[str, Any]] = {}

    for role in manifest.get("roles", []):
        packet = _read(Path(manifest["role_packets"][role]))
        response = _read(Path(manifest["role_response_templates"][role]))
        if response.get("packet_id") != packet_id or response.get("role") != role:
            raise AnalysisValidationError(f"{role} response does not match this packet")
        if response.get("status") not in _STATUSES:
            raise AnalysisValidationError(f"{role} has invalid or placeholder status")
        if not str(response.get("data_quality_assessment", "")).strip():
            raise AnalysisValidationError(f"{role} must assess data quality")
        allowed = {
            item["evidence_id"] for item in packet.get("computed_sections", {}).values()
        } | {item["article_id"] for item in packet.get("relevant_news", [])}
        _check_ids(response.get("evidence_ids"), allowed, role)
        _check_findings(response, allowed, role)
        if response["status"] != "blocked" and not str(
            response.get("forward_view", {}).get("thesis", "")
        ).strip():
            raise AnalysisValidationError(f"{role} must provide a forward-view thesis")
        responses[role] = response

    synthesis = _read(Path(manifest["synthesis_response_template"]))
    if synthesis.get("packet_id") != packet_id:
        raise AnalysisValidationError("synthesis response does not match this packet")
    if synthesis.get("status") not in _STATUSES:
        raise AnalysisValidationError("synthesis has invalid or placeholder status")
    if not str(synthesis.get("headline", "")).strip() or not str(
        synthesis.get("executive_summary", "")
    ).strip():
        raise AnalysisValidationError("synthesis requires a headline and executive summary")
    _check_ids(synthesis.get("evidence_ids"), registry, "synthesis")

    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "packet_id": packet_id,
        "product": manifest.get("product"),
        "trade_date": manifest.get("trade_date"),
        "specialist_analyses": responses,
        "synthesis": synthesis,
        "status": synthesis["status"],
        "shadow_mode": True,
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
        "", "> Shadow workflow output — not approved for automatic delivery.", "",
        f"## {synthesis.get('headline') or 'Executive Summary'}", "",
        synthesis.get("executive_summary", ""), "",
    ]
    for role, response in result["specialist_analyses"].items():
        title = role.replace("_", " ").title()
        lines.extend([f"## {title}", "", f"Status: {response['status']}", ""])
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
                    lines.extend(f"- {item if isinstance(item, str) else json.dumps(item)}" for item in value)
                else:
                    lines.append(str(value))
                lines.append("")
        view = response.get("forward_view", {})
        lines.extend(["### Forward view", "", str(view.get("thesis", "")), ""])
    lines.extend(["## Evidence IDs", "", ", ".join(synthesis.get("evidence_ids", [])), ""])
    return "\n".join(lines)

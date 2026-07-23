"""Inspectable, model-free audit artifacts for agent-orchestrated analysis runs."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MONITOR_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(path: Path | None) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.exists() else ""


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, indent=2, default=str))
    temp.replace(path)


def monitor_paths(state: dict[str, Any]) -> tuple[Path, Path, Path]:
    run_dir = Path(state["manifest_path"]).parent
    return (
        run_dir / "workflow_events.jsonl",
        run_dir / "workflow_monitor.json",
        run_dir / "workflow_monitor.md",
    )


def reset_events(state: dict[str, Any]) -> None:
    events_path, _, _ = monitor_paths(state)
    events_path.unlink(missing_ok=True)


def record_event(state: dict[str, Any], event: str, **details: Any) -> None:
    """Append one controller-observed event; this never invokes or introspects a model."""
    events_path, _, _ = monitor_paths(state)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "timestamp": _now(),
        "run_id": state.get("run_id"),
        "product": state.get("product"),
        "trade_date": state.get("trade_date"),
        "phase": state.get("phase"),
        "event": event,
        **details,
    }
    with events_path.open("a") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")


def archive_invalid_response(path: Path, attempt: int) -> Path | None:
    """Preserve rejected agent output before the controller clears the response slot."""
    if not path.exists():
        return None
    archive = path.with_name(f"{path.stem}.invalid-attempt-{attempt}{path.suffix}")
    archive.write_bytes(path.read_bytes())
    return archive


def _artifact(path_value: str | None, *, include_text: bool = False) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(path_value)
    item: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "sha256": _hash(path),
    }
    if include_text and path.exists():
        item["content"] = path.read_text(errors="replace")
    return item


def _events(path: Path, run_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("run_id") == run_id:
            result.append(item)
    return result


def _agent_record(
    *, name: str, agent_type: str, status: str, task: str | None,
    packet: str | None, template: str | None, response: str | None,
    corrections: int = 0, last_error: str = "", knowledge_pack: str | None = None,
) -> dict[str, Any]:
    inputs = {
        "task": _artifact(task, include_text=True),
        "evidence_packet": _artifact(packet),
        "response_schema": _artifact(template),
    }
    if knowledge_pack:
        inputs["knowledge_pack"] = {"path": knowledge_pack, "exists": Path(knowledge_pack).exists()}
    return {
        "name": name,
        "agent_type": agent_type,
        "status": status,
        "corrections": corrections,
        "last_validation_error": last_error,
        "inputs": inputs,
        "output": _artifact(response, include_text=True),
    }


def build_monitor(state_path: Path) -> dict[str, Any]:
    state = json.loads(state_path.read_text())
    manifest_path = Path(state["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    repo_root = Path(state.get("qc", {}).get("repo_root") or manifest_path.parents[4])
    knowledge_pack = str(repo_root / "knowledge" / manifest.get("knowledge_pack", ""))
    agents = [
        _agent_record(
            name="data_quality", agent_type="curvelens_data_qc",
            status=state.get("qc", {}).get("status", "pending"),
            task=state.get("qc", {}).get("task_path"),
            packet=state.get("qc", {}).get("packet_path"),
            template=state.get("qc", {}).get("template_path"),
            response=state.get("qc", {}).get("response_path"),
            corrections=int(state.get("qc", {}).get("corrections", 0)),
            last_error=state.get("qc", {}).get("last_error", ""),
        )
    ]
    for role in manifest.get("roles", []):
        role_state = state.get("roles", {}).get(role, {})
        agents.append(_agent_record(
            name=role, agent_type="curvelens_specialist",
            status=role_state.get("status", "pending"),
            task=role_state.get("task_path"),
            packet=manifest.get("role_packets", {}).get(role),
            template=manifest.get("role_response_templates", {}).get(role),
            response=manifest.get("role_response_paths", {}).get(role),
            corrections=int(role_state.get("corrections", 0)),
            last_error=role_state.get("last_error", ""), knowledge_pack=knowledge_pack,
        ))
    synthesis = state.get("synthesis", {})
    agents.append(_agent_record(
        name="synthesis", agent_type="curvelens_synthesizer",
        status=synthesis.get("status", "pending"), task=synthesis.get("task_path"),
        packet=str(manifest_path), template=manifest.get("synthesis_response_template"),
        response=manifest.get("synthesis_response_path"),
        corrections=int(synthesis.get("corrections", 0)),
        last_error=synthesis.get("last_error", ""),
    ))
    events_path, json_path, md_path = monitor_paths(state)
    monitor = {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": _now(),
        "run_id": state["run_id"],
        "product": state["product"],
        "trade_date": state["trade_date"],
        "phase": state["phase"],
        "delivery_queued": bool(state.get("delivery_queued", False)),
        "manifest": _artifact(str(manifest_path)),
        "agents": agents,
        "events": _events(events_path, state["run_id"]),
        "scope_note": (
            "This monitor records controller-visible instructions, allowed inputs, outputs, and "
            "validation events. It does not expose hidden chain-of-thought or host-runtime tool telemetry."
        ),
    }
    _write_json_atomic(json_path, monitor)
    md_path.write_text(_render_markdown(monitor))
    return monitor


def _link(item: dict[str, Any] | None, label: str) -> str:
    if not item:
        return f"{label}: not created"
    return f"{label}: [`{Path(item['path']).name}`]({item['path']}) · sha256 `{item.get('sha256', '')[:12]}`"


def _fenced(content: str, language: str) -> list[str]:
    """Fence untrusted text without allowing embedded backticks to escape it."""
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", content.rstrip(), fence]


def _render_markdown(monitor: dict[str, Any]) -> str:
    lines = [
        f"# {str(monitor['product']).upper()} Workflow Monitor — {monitor['trade_date']}", "",
        f"- Phase: **{monitor['phase']}**",
        f"- Run ID: `{monitor['run_id']}`",
        f"- Delivery queued: **{str(monitor['delivery_queued']).lower()}**", "",
        f"> {monitor['scope_note']}", "", "## Timeline", "",
    ]
    if monitor["events"]:
        for event in monitor["events"]:
            actor = f" · {event['actor']}" if event.get("actor") else ""
            detail = f" — {event['detail']}" if event.get("detail") else ""
            lines.append(f"- `{event['timestamp']}` · **{event['event']}**{actor}{detail}")
    else:
        lines.append("- No controller events recorded yet.")
    lines.extend(["", "## Agent inspections", ""])
    for agent in monitor["agents"]:
        lines.extend([
            f"### {agent['name'].replace('_', ' ').title()}", "",
            f"- Agent type: `{agent['agent_type']}`",
            f"- Status: **{agent['status']}**",
            f"- Corrections: **{agent['corrections']}**",
        ])
        if agent.get("last_validation_error"):
            lines.append(f"- Last validation error: `{agent['last_validation_error']}`")
        inputs = agent["inputs"]
        lines.extend([
            f"- {_link(inputs.get('task'), 'Task instructions')}",
            f"- {_link(inputs.get('evidence_packet'), 'Evidence packet')}",
            f"- {_link(inputs.get('response_schema'), 'Response schema')}",
        ])
        if inputs.get("knowledge_pack"):
            lines.append(f"- Knowledge pack: `{inputs['knowledge_pack']['path']}`")
        lines.append(f"- {_link(agent.get('output'), 'Agent output')}")
        task = inputs.get("task") or {}
        if task.get("content"):
            lines.extend(["", "<details><summary>Exact assigned task</summary>", ""])
            lines.extend(_fenced(task["content"], "text"))
            lines.append("</details>")
        output = agent.get("output") or {}
        if output.get("content"):
            lines.extend(["", "<details><summary>Exact submitted response</summary>", ""])
            lines.extend(_fenced(output["content"], "json"))
            lines.append("</details>")
        lines.append("")
    return "\n".join(lines)

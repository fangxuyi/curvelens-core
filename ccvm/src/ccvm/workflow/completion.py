"""Check persisted workflow identity and the artifacts behind completion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .finalize import AnalysisValidationError

ARTIFACTS = {"analysis_json": "analysis.json", "analysis_md": "analysis.md",
             "statistics_md": "statistics.md", "mobile_md": "mobile.md"}


def validate_manifest_identity(path: Path, product: str, trade_date: str, run_dir: Path) -> dict:
    if path.resolve() != (run_dir / "manifest.json").resolve():
        raise AnalysisValidationError("manifest path does not belong to this product/date run")
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict):
        raise AnalysisValidationError("manifest must be an object")
    if manifest.get("product") != product or manifest.get("trade_date") != trade_date:
        raise AnalysisValidationError("manifest product/date does not match the invocation")
    return manifest


def validate_run_identity(state: dict, product: str, trade_date: str, run_dir: Path) -> None:
    if state.get("product") != product or state.get("trade_date") != trade_date:
        raise AnalysisValidationError("saved workflow product/date does not match the invocation")
    if not isinstance(state.get("manifest_path"), str) or not state["manifest_path"]:
        raise AnalysisValidationError("saved workflow is missing its manifest path")
    manifest = validate_manifest_identity(Path(state["manifest_path"]), product, trade_date, run_dir)
    if not state.get("packet_id") or state["packet_id"] != manifest.get("packet_id"):
        raise AnalysisValidationError("saved workflow packet identity does not match its manifest")


def artifact_hashes(state: dict, data_root: Path) -> dict[str, str]:
    output = data_root / "analysis" / f"trade_date={state['trade_date']}"
    hashes = {}
    for field, filename in ARTIFACTS.items():
        value = state.get(field)
        if not isinstance(value, str) or not value:
            raise AnalysisValidationError(f"completed workflow is missing {field}")
        path = Path(value)
        if path.resolve() != (output / filename).resolve():
            raise AnalysisValidationError(f"{field} path does not belong to this product/date")
        if not path.is_file() or not path.stat().st_size:
            raise AnalysisValidationError(f"completed workflow artifact is missing or empty: {field}")
        hashes[field] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def verify_completed_artifacts(state: dict, data_root: Path) -> str:
    if state.get("phase") != "COMPLETE":
        raise AnalysisValidationError("workflow is not complete")
    current = artifact_hashes(state, data_root)
    analysis = json.loads(Path(state["analysis_json"]).read_text())
    if not isinstance(analysis, dict) or any(
        analysis.get(field) != state.get(field) for field in ("product", "trade_date", "packet_id")
    ):
        raise AnalysisValidationError("analysis artifact identity does not match the completed workflow")
    if "artifact_sha256" not in state:
        # Legacy completion records predate digests. Do not stamp today's bytes as
        # trusted historical hashes or require an unrelated analysis restart.
        return "legacy_identity_and_presence"
    expected = state["artifact_sha256"]
    if not isinstance(expected, dict) or set(expected) != set(ARTIFACTS):
        raise AnalysisValidationError("completed workflow has an incomplete artifact digest record")
    if current != expected:
        raise AnalysisValidationError("completed workflow artifact content changed after finalization")
    return "sha256_verified"

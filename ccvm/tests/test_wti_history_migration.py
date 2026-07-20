"""Safety and integrity tests for the legacy WTI history migrator."""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
sys.path.insert(0, str(AGENT_DIR))
import migrate_wti_history as migration  # noqa: E402

from ccvm.storage.manifest_db import ManifestDB  # noqa: E402


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


def _make_root(path: Path) -> Path:
    for name in migration._REQUIRED_SOURCE_DIRS:
        (path / name).mkdir(parents=True, exist_ok=True)
    _json(path / "agent_outbox" / "pending.json", [])
    _json(path / "agent_outbox" / "delivered.json", [])
    ManifestDB(path / "manifests" / "manifest.duckdb")
    return path


def _add_manifest_entry(root: Path, filename: str, content: bytes) -> str:
    raw_path = root / "raw" / "fixture" / filename
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    run_id = str(uuid.uuid4())
    entry_id = str(uuid.uuid4())
    db = ManifestDB(root / "manifests" / "manifest.duckdb")
    db.start_run(run_id, "fixture", "2026-07-17")
    db.insert_manifest_entry({
        "entry_id": entry_id,
        "source_id": "fixture",
        "raw_path": str(raw_path),
        "sha256": digest,
        "byte_size": len(content),
        "retrieved_at": datetime.now(timezone.utc),
        "trade_date": "2026-07-17",
        "source_url": "fixture",
        "http_status": 200,
        "content_type": "application/json",
        "collection_run_id": run_id,
    })
    db.complete_run(run_id, "success", 1, 0, 0, 0)
    return entry_id


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, str, str]:
    source = _make_root(tmp_path / "legacy" / "data")
    destination = _make_root(tmp_path / "core" / "data" / "products" / "wti")

    source_entry = _add_manifest_entry(source, "source.json", b'{"source": true}')
    destination_entry = _add_manifest_entry(
        destination, "destination-only.json", b'{"destination": true}'
    )

    _json(source / "state" / "scenario_state.json", {"version": "source"})
    _json(destination / "state" / "scenario_state.json", {"version": "old"})
    _json(source / "reports" / "2026-07-17.json", {"date": "2026-07-17"})

    _json(source / "agent_outbox" / "delivered.json", [
        {"id": "2026-07-17:DAILY_BRIEF", "delivered_at": "source"},
    ])
    _json(source / "agent_outbox" / "pending.json", [
        {"id": "2026-07-17:PRIORITY_ALERT", "text": "source pending"},
    ])
    _json(destination / "agent_outbox" / "delivered.json", [
        {"id": "2026-07-17:PRIORITY_ALERT", "delivered_at": "destination"},
        {"id": "2026-07-12:TEST", "delivered_at": "destination"},
    ])
    _json(destination / "agent_outbox" / "pending.json", [
        {"id": "2026-07-18:DAILY_BRIEF", "text": "destination pending"},
    ])
    return source, destination, source_entry, destination_entry


def test_dry_run_reports_changes_without_writing(roots):
    source, destination, _source_entry, _destination_entry = roots
    before = (destination / "state" / "scenario_state.json").read_bytes()
    result = migration.migrate(source, destination, apply=False)

    assert result["result"] == "DRY_RUN"
    assert result["files"]["copy"] >= 1
    assert "state/scenario_state.json" in result["files"]["overwrite_samples"]
    assert result["outbox"]["destination_delivered_after"] == 3
    assert (destination / "state" / "scenario_state.json").read_bytes() == before
    assert not (destination / ".migration_backups").exists()


def test_apply_copies_backs_up_merges_and_rewrites_manifest(roots):
    source, destination, source_entry, destination_entry = roots
    result = migration.migrate(source, destination, apply=True)

    assert result["result"] == "MIGRATED"
    assert result["source_deleted"] is False
    assert result["verification"]["source_manifest_rows_verified"] == 1
    assert json.loads((destination / "state" / "scenario_state.json").read_text()) == {
        "version": "source"
    }
    assert (destination / "reports" / "2026-07-17.json").exists()

    backup = Path(result["backup_root"])
    assert json.loads((backup / "state" / "scenario_state.json").read_text()) == {
        "version": "old"
    }
    assert Path(result["report_path"]).is_file()

    delivered = json.loads((destination / "agent_outbox" / "delivered.json").read_text())
    pending = json.loads((destination / "agent_outbox" / "pending.json").read_text())
    assert {item["id"] for item in delivered} == {
        "2026-07-17:DAILY_BRIEF", "2026-07-17:PRIORITY_ALERT", "2026-07-12:TEST",
    }
    assert {item["id"] for item in pending} == {"2026-07-18:DAILY_BRIEF"}

    connection = duckdb.connect(
        str(destination / "manifests" / "manifest.duckdb"), read_only=True
    )
    try:
        rows = dict(connection.execute("SELECT entry_id, raw_path FROM raw_manifest").fetchall())
    finally:
        connection.close()
    assert source_entry in rows
    assert destination_entry in rows
    assert rows[source_entry] == str(destination / "raw" / "fixture" / "source.json")
    assert rows[destination_entry].startswith(str(destination))


def test_validate_roots_rejects_nested_destination(tmp_path: Path):
    source = _make_root(tmp_path / "legacy" / "data")
    with pytest.raises(migration.MigrationError, match="contain one another"):
        migration.validate_roots(source, source / "products" / "wti")


def test_quiescence_check_rejects_manifest_wal(roots):
    source, destination, _source_entry, _destination_entry = roots
    wal = Path(str(source / "manifests" / "manifest.duckdb") + ".wal")
    wal.touch()
    with pytest.raises(migration.MigrationError, match="WAL"):
        migration.require_quiescent_manifests(source, destination)

#!/usr/bin/env python3
"""Migrate a legacy single-product WTI data root into CurveLens Core.

The command is dry-run-only unless both ``--apply`` and
``--confirm-source-stopped`` are supplied. It never deletes the source.

Besides copying historical artifacts, it handles the two pieces a plain file
copy cannot safely migrate:

* merge pending/delivered outbox ledgers by message id, with delivered ids
  removed from pending so historical briefs cannot be sent twice;
* merge the DuckDB manifest and rewrite absolute raw paths from the legacy
  repository to the Core WTI runtime root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ccvm" / "src"))

from ccvm.runtime import data_dir  # noqa: E402
from ccvm.storage.manifest_db import ManifestDB  # noqa: E402


class MigrationError(RuntimeError):
    """Raised when migration safety or verification fails."""


_REQUIRED_SOURCE_DIRS = (
    "raw", "bronze", "silver", "gold", "manifests", "reports",
    "agent_outbox", "state",
)
_SPECIAL_FILES = {
    Path("manifests/manifest.duckdb"),
    Path("manifests/manifest.duckdb.wal"),
    Path("agent_outbox/pending.json"),
    Path("agent_outbox/delivered.json"),
}
_IGNORED_NAMES = {".DS_Store"}
_IGNORED_TOP_LEVEL = {".migration_backups", ".migration_reports"}

_RAW_COLUMNS = (
    "entry_id", "source_id", "raw_path", "sha256", "byte_size",
    "retrieved_at", "trade_date", "source_url", "http_status",
    "content_type", "collection_run_id",
)
_RUN_COLUMNS = (
    "run_id", "started_at", "completed_at", "source_id", "as_of_date",
    "status", "success_count", "warning_count", "failure_count",
    "skipped_count", "notes",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_roots(source: Path, destination: Path) -> tuple[Path, Path]:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if source == destination:
        raise MigrationError("source and destination must be different")
    if _is_relative_to(source, destination) or _is_relative_to(destination, source):
        raise MigrationError("source and destination may not contain one another")
    if not source.is_dir():
        raise MigrationError(f"source data root does not exist: {source}")
    missing = [name for name in _REQUIRED_SOURCE_DIRS if not (source / name).is_dir()]
    if missing:
        raise MigrationError(f"source is not a complete WTI data root; missing: {missing}")
    if not (source / "manifests" / "manifest.duckdb").is_file():
        raise MigrationError("source manifest database is missing")
    return source, destination


def _iter_regular_files(root: Path, *, include_special: bool = False) -> Iterable[tuple[Path, Path]]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise MigrationError(f"symlinks are not accepted in migration data: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in _IGNORED_TOP_LEVEL:
            continue
        if path.name in _IGNORED_NAMES:
            continue
        if not include_special and relative in _SPECIAL_FILES:
            continue
        yield relative, path


def build_file_plan(source: Path, destination: Path) -> dict:
    source_files = dict(_iter_regular_files(source))
    copy: list[str] = []
    overwrite: list[str] = []
    unchanged: list[str] = []
    copy_bytes = 0
    overwrite_bytes = 0
    for relative, source_path in source_files.items():
        destination_path = destination / relative
        if not destination_path.exists():
            copy.append(relative.as_posix())
            copy_bytes += source_path.stat().st_size
            continue
        if not destination_path.is_file() or destination_path.is_symlink():
            raise MigrationError(f"destination conflict is not a regular file: {destination_path}")
        same = (
            source_path.stat().st_size == destination_path.stat().st_size
            and _sha256(source_path) == _sha256(destination_path)
        )
        if same:
            unchanged.append(relative.as_posix())
        else:
            overwrite.append(relative.as_posix())
            overwrite_bytes += source_path.stat().st_size
    return {
        "copy": copy,
        "overwrite": overwrite,
        "unchanged": unchanged,
        "copy_bytes": copy_bytes,
        "overwrite_bytes": overwrite_bytes,
    }


def _load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise MigrationError(f"unreadable outbox ledger {path}: {exc}") from exc
    if not isinstance(value, list):
        raise MigrationError(f"outbox ledger must be a JSON list: {path}")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise MigrationError(f"outbox item missing string id in {path}")
        if item["id"] in seen:
            raise MigrationError(f"duplicate outbox id {item['id']!r} in {path}")
        seen.add(item["id"])
    return value


def _merge_items(destination_items: list[dict], source_items: list[dict]) -> list[dict]:
    # Dict assignment preserves the destination ordering while allowing the
    # authoritative source value to replace a matching id in place.
    merged = {item["id"]: item for item in destination_items}
    for item in source_items:
        merged[item["id"]] = item
    return list(merged.values())


def merged_ledgers(source: Path, destination: Path) -> tuple[list[dict], list[dict], dict]:
    source_pending = _load_ledger(source / "agent_outbox" / "pending.json")
    source_delivered = _load_ledger(source / "agent_outbox" / "delivered.json")
    destination_pending = _load_ledger(destination / "agent_outbox" / "pending.json")
    destination_delivered = _load_ledger(destination / "agent_outbox" / "delivered.json")

    delivered = _merge_items(destination_delivered, source_delivered)
    delivered_ids = {item["id"] for item in delivered}
    pending = [
        item for item in _merge_items(destination_pending, source_pending)
        if item["id"] not in delivered_ids
    ]
    summary = {
        "source_pending": len(source_pending),
        "source_delivered": len(source_delivered),
        "destination_pending_before": len(destination_pending),
        "destination_delivered_before": len(destination_delivered),
        "destination_pending_after": len(pending),
        "destination_delivered_after": len(delivered),
    }
    return pending, delivered, summary


def _manifest_rows(db_path: Path) -> tuple[list[tuple], list[tuple]]:
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        raw_columns = tuple(row[0] for row in connection.execute("DESCRIBE raw_manifest").fetchall())
        run_columns = tuple(row[0] for row in connection.execute("DESCRIBE collection_runs").fetchall())
        if raw_columns != _RAW_COLUMNS or run_columns != _RUN_COLUMNS:
            raise MigrationError(
                f"manifest schema mismatch in {db_path}: "
                f"raw={raw_columns}, runs={run_columns}"
            )
        running = connection.execute(
            "SELECT count(*) FROM collection_runs WHERE status = 'running'"
        ).fetchone()[0]
        if running:
            raise MigrationError(f"source manifest has {running} running collection job(s)")
        raw = connection.execute("SELECT * FROM raw_manifest ORDER BY entry_id").fetchall()
        runs = connection.execute("SELECT * FROM collection_runs ORDER BY run_id").fetchall()
        return raw, runs
    finally:
        connection.close()


def manifest_plan(source: Path, destination: Path) -> dict:
    source_raw, source_runs = _manifest_rows(source / "manifests" / "manifest.duckdb")
    destination_db = destination / "manifests" / "manifest.duckdb"
    if destination_db.exists():
        destination_raw, destination_runs = _manifest_rows(destination_db)
    else:
        destination_raw, destination_runs = [], []
    return {
        "source_raw_rows": len(source_raw),
        "source_run_rows": len(source_runs),
        "destination_raw_rows_before": len(destination_raw),
        "destination_run_rows_before": len(destination_runs),
        "destination_raw_rows_after_minimum": len(
            {row[0] for row in destination_raw} | {row[0] for row in source_raw}
        ),
        "destination_run_rows_after_minimum": len(
            {row[0] for row in destination_runs} | {row[0] for row in source_runs}
        ),
        "source_wal_present": Path(str(source / "manifests" / "manifest.duckdb") + ".wal").exists(),
        "destination_wal_present": Path(str(destination_db) + ".wal").exists(),
    }


def require_quiescent_manifests(source: Path, destination: Path) -> None:
    databases = [
        source / "manifests" / "manifest.duckdb",
        destination / "manifests" / "manifest.duckdb",
    ]
    active_wals = [Path(str(path) + ".wal") for path in databases if Path(str(path) + ".wal").exists()]
    if active_wals:
        raise MigrationError(
            "manifest WAL file(s) are present; stop both WTI runtimes and let "
            f"DuckDB close cleanly before rerunning: {active_wals}"
        )


def _backup(path: Path, destination: Path, backup_root: Path) -> None:
    if not path.exists():
        return
    relative = path.relative_to(destination)
    backup_path = backup_root / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.migration-{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.migration-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_files(source: Path, destination: Path, plan: dict, backup_root: Path) -> None:
    for relative_text in plan["copy"] + plan["overwrite"]:
        relative = Path(relative_text)
        destination_path = destination / relative
        if relative_text in plan["overwrite"]:
            _backup(destination_path, destination, backup_root)
        _atomic_copy(source / relative, destination_path)


def apply_ledgers(
    destination: Path,
    pending: list[dict],
    delivered: list[dict],
    backup_root: Path,
) -> None:
    for name, value in (("pending.json", pending), ("delivered.json", delivered)):
        path = destination / "agent_outbox" / name
        _backup(path, destination, backup_root)
        _atomic_json(path, value)


def _rewrite_raw_row(row: tuple, source: Path, destination: Path) -> tuple:
    raw_path = Path(row[2]).expanduser().resolve()
    try:
        relative = raw_path.relative_to(source)
    except ValueError as exc:
        raise MigrationError(f"manifest raw_path is outside source root: {raw_path}") from exc
    rewritten = list(row)
    rewritten[2] = str(destination / relative)
    return tuple(rewritten)


def apply_manifest(source: Path, destination: Path, backup_root: Path) -> None:
    source_db = source / "manifests" / "manifest.duckdb"
    destination_db = destination / "manifests" / "manifest.duckdb"
    source_raw, source_runs = _manifest_rows(source_db)
    rewritten_raw = [_rewrite_raw_row(row, source, destination) for row in source_raw]

    destination_db.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_db.with_name(
        f".{destination_db.name}.migration-{uuid.uuid4().hex}.tmp"
    )
    try:
        if destination_db.exists():
            shutil.copy2(destination_db, temporary)
        else:
            ManifestDB(temporary)

        connection = duckdb.connect(str(temporary))
        try:
            for row in source_runs:
                connection.execute("DELETE FROM collection_runs WHERE run_id = ?", [row[0]])
                connection.execute(
                    f"INSERT INTO collection_runs ({','.join(_RUN_COLUMNS)}) "
                    f"VALUES ({','.join('?' for _ in _RUN_COLUMNS)})",
                    list(row),
                )
            for row in rewritten_raw:
                connection.execute("DELETE FROM raw_manifest WHERE entry_id = ?", [row[0]])
                connection.execute(
                    f"INSERT INTO raw_manifest ({','.join(_RAW_COLUMNS)}) "
                    f"VALUES ({','.join('?' for _ in _RAW_COLUMNS)})",
                    list(row),
                )

            external = []
            for entry_id, raw_path in connection.execute(
                "SELECT entry_id, raw_path FROM raw_manifest"
            ).fetchall():
                candidate = Path(raw_path).expanduser().resolve()
                if not _is_relative_to(candidate, destination):
                    external.append((entry_id, raw_path))
            if external:
                raise MigrationError(
                    f"merged manifest would retain {len(external)} path(s) outside destination"
                )
        finally:
            connection.close()

        _backup(destination_db, destination, backup_root)
        os.replace(temporary, destination_db)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_migration(source: Path, destination: Path) -> dict:
    checked_files = 0
    for relative, source_path in _iter_regular_files(source):
        destination_path = destination / relative
        if not destination_path.is_file():
            raise MigrationError(f"missing migrated file: {destination_path}")
        if source_path.stat().st_size != destination_path.stat().st_size:
            raise MigrationError(f"migrated file size mismatch: {relative}")
        if _sha256(source_path) != _sha256(destination_path):
            raise MigrationError(f"migrated file checksum mismatch: {relative}")
        checked_files += 1

    source_raw, source_runs = _manifest_rows(source / "manifests" / "manifest.duckdb")
    destination_raw, destination_runs = _manifest_rows(
        destination / "manifests" / "manifest.duckdb"
    )
    destination_raw_by_id = {row[0]: row for row in destination_raw}
    destination_run_ids = {row[0] for row in destination_runs}
    for row in source_raw:
        migrated = destination_raw_by_id.get(row[0])
        if migrated is None:
            raise MigrationError(f"manifest entry not migrated: {row[0]}")
        expected = _rewrite_raw_row(row, source, destination)
        if tuple(migrated) != expected:
            raise MigrationError(f"manifest entry differs after migration: {row[0]}")
        path = Path(migrated[2])
        if not path.is_file() or _sha256(path) != migrated[3]:
            raise MigrationError(f"manifest checksum/path verification failed: {path}")
    missing_runs = {row[0] for row in source_runs} - destination_run_ids
    if missing_runs:
        raise MigrationError(f"collection runs not migrated: {sorted(missing_runs)[:5]}")

    pending = _load_ledger(destination / "agent_outbox" / "pending.json")
    delivered = _load_ledger(destination / "agent_outbox" / "delivered.json")
    delivered_ids = {item["id"] for item in delivered}
    if delivered_ids & {item["id"] for item in pending}:
        raise MigrationError("pending and delivered ledgers overlap after migration")
    source_delivered_ids = {
        item["id"] for item in _load_ledger(source / "agent_outbox" / "delivered.json")
    }
    if not source_delivered_ids.issubset(delivered_ids):
        raise MigrationError("source delivery history is incomplete after migration")
    return {
        "files_checksum_verified": checked_files,
        "source_manifest_rows_verified": len(source_raw),
        "source_collection_runs_verified": len(source_runs),
        "destination_pending": len(pending),
        "destination_delivered": len(delivered),
    }


def _source_snapshot(source: Path) -> dict[str, tuple[int, int]]:
    return {
        relative.as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for relative, path in _iter_regular_files(source, include_special=True)
    }


def migrate(source: Path, destination: Path, *, apply: bool) -> dict:
    source, destination = validate_roots(source, destination)
    before = _source_snapshot(source)
    file_plan = build_file_plan(source, destination)
    pending, delivered, ledger_summary = merged_ledgers(source, destination)
    manifest_summary = manifest_plan(source, destination)
    result = {
        "result": "DRY_RUN" if not apply else "MIGRATED",
        "source": str(source),
        "destination": str(destination),
        "files": {
            "copy": len(file_plan["copy"]),
            "overwrite": len(file_plan["overwrite"]),
            "unchanged": len(file_plan["unchanged"]),
            "copy_bytes": file_plan["copy_bytes"],
            "overwrite_bytes": file_plan["overwrite_bytes"],
            "overwrite_samples": file_plan["overwrite"][:20],
        },
        "outbox": ledger_summary,
        "manifest": manifest_summary,
        "source_deleted": False,
    }
    if not apply:
        return result

    require_quiescent_manifests(source, destination)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = destination / ".migration_backups" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    apply_files(source, destination, file_plan, backup_root)
    apply_ledgers(destination, pending, delivered, backup_root)
    apply_manifest(source, destination, backup_root)

    after = _source_snapshot(source)
    if before != after:
        raise MigrationError(
            "source changed during migration; leave the source stopped and rerun safely"
        )
    result["backup_root"] = str(backup_root)
    result["verification"] = verify_migration(source, destination)
    report_path = destination / ".migration_reports" / f"{stamp}.json"
    _atomic_json(report_path, result)
    result["report_path"] = str(report_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply a legacy CurveLens WTI history migration"
    )
    parser.add_argument(
        "--source", required=True, type=Path,
        help="Legacy WTI data root (for example /path/to/CurveLens/ccvm/data)",
    )
    parser.add_argument(
        "--destination", type=Path,
        help="Core WTI data root (default: selected CCVM_PRODUCT runtime root)",
    )
    parser.add_argument("--apply", action="store_true", help="Perform the migration")
    parser.add_argument(
        "--confirm-runtimes-stopped", action="store_true",
        help="Confirm both legacy and Core WTI agents/schedules are stopped",
    )
    args = parser.parse_args()

    if os.environ.get("CCVM_PRODUCT") != "wti":
        parser.error("set CCVM_PRODUCT=wti explicitly")
    if args.apply and not args.confirm_runtimes_stopped:
        parser.error("--apply requires --confirm-runtimes-stopped")

    destination = args.destination or data_dir()
    try:
        result = migrate(args.source, destination, apply=args.apply)
    except MigrationError as exc:
        print(json.dumps({"result": "ERROR", "detail": str(exc)}))
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

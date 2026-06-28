from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb


class ManifestDB:
    """DuckDB-backed manifest tracking raw files and collection runs."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))

    def _init_tables(self) -> None:
        con = self._connect()
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS raw_manifest (
                    entry_id    VARCHAR PRIMARY KEY,
                    source_id   VARCHAR NOT NULL,
                    raw_path    VARCHAR NOT NULL,
                    sha256      VARCHAR NOT NULL,
                    byte_size   BIGINT  NOT NULL,
                    retrieved_at VARCHAR NOT NULL,
                    trade_date  VARCHAR,
                    source_url  VARCHAR,
                    http_status INTEGER,
                    content_type VARCHAR,
                    collection_run_id VARCHAR NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS collection_runs (
                    run_id       VARCHAR PRIMARY KEY,
                    started_at   VARCHAR NOT NULL,
                    completed_at VARCHAR,
                    source_id    VARCHAR NOT NULL,
                    as_of_date   VARCHAR NOT NULL,
                    status       VARCHAR NOT NULL,
                    success_count  INTEGER DEFAULT 0,
                    warning_count  INTEGER DEFAULT 0,
                    failure_count  INTEGER DEFAULT 0,
                    skipped_count  INTEGER DEFAULT 0,
                    notes        VARCHAR
                )
            """)
        finally:
            con.close()

    def sha256_exists(self, sha256: str) -> bool:
        con = self._connect()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM raw_manifest WHERE sha256 = ?", [sha256]
            ).fetchone()[0]
            return count > 0
        finally:
            con.close()

    def sha256_exists_for_date(self, sha256: str, trade_date: str) -> bool:
        """True if this exact (sha256, trade_date) pair is already in the manifest."""
        con = self._connect()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM raw_manifest WHERE sha256 = ? AND trade_date = ?",
                [sha256, trade_date],
            ).fetchone()[0]
            return count > 0
        finally:
            con.close()

    def get_entry_by_sha256(self, sha256: str) -> Optional[dict]:
        """Return the first manifest entry matching this sha256, or None."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM raw_manifest WHERE sha256 = ? LIMIT 1", [sha256]
            ).fetchone()
            if row is None:
                return None
            cols = [d[0] for d in con.description]
            return dict(zip(cols, row))
        finally:
            con.close()

    def insert_manifest_entry(self, entry: dict) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO raw_manifest
                    (entry_id, source_id, raw_path, sha256, byte_size, retrieved_at,
                     trade_date, source_url, http_status, content_type, collection_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    entry["entry_id"],
                    entry["source_id"],
                    entry["raw_path"],
                    entry["sha256"],
                    entry["byte_size"],
                    entry["retrieved_at"].isoformat() if hasattr(entry["retrieved_at"], "isoformat") else entry["retrieved_at"],
                    entry.get("trade_date"),
                    entry.get("source_url"),
                    entry.get("http_status"),
                    entry.get("content_type"),
                    entry["collection_run_id"],
                ],
            )
        finally:
            con.close()

    def start_run(self, run_id: str, source_id: str, as_of_date: str) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO collection_runs
                    (run_id, started_at, source_id, as_of_date, status)
                VALUES (?, ?, ?, ?, 'running')
                """,
                [run_id, datetime.now(timezone.utc).isoformat(), source_id, as_of_date],
            )
        finally:
            con.close()

    def complete_run(
        self,
        run_id: str,
        status: str,
        success: int,
        warning: int,
        failure: int,
        skipped: int,
        notes: Optional[str] = None,
    ) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                UPDATE collection_runs
                SET completed_at  = ?,
                    status        = ?,
                    success_count = ?,
                    warning_count = ?,
                    failure_count = ?,
                    skipped_count = ?,
                    notes         = ?
                WHERE run_id = ?
                """,
                [
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    success,
                    warning,
                    failure,
                    skipped,
                    notes,
                    run_id,
                ],
            )
        finally:
            con.close()

    def get_run_history(self, source_id: Optional[str] = None) -> list[dict]:
        con = self._connect()
        try:
            if source_id:
                rows = con.execute(
                    "SELECT * FROM collection_runs WHERE source_id = ? ORDER BY started_at",
                    [source_id],
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM collection_runs ORDER BY started_at"
                ).fetchall()
            cols = [d[0] for d in con.description]
            return [dict(zip(cols, row)) for row in rows]
        finally:
            con.close()

    def get_manifest_entries(self, source_id: Optional[str] = None) -> list[dict]:
        con = self._connect()
        try:
            if source_id:
                rows = con.execute(
                    "SELECT * FROM raw_manifest WHERE source_id = ? ORDER BY retrieved_at",
                    [source_id],
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM raw_manifest ORDER BY retrieved_at"
                ).fetchall()
            cols = [d[0] for d in con.description]
            return [dict(zip(cols, row)) for row in rows]
        finally:
            con.close()

    def get_manifest_entry_count(self) -> int:
        con = self._connect()
        try:
            return con.execute("SELECT COUNT(*) FROM raw_manifest").fetchone()[0]
        finally:
            con.close()

    def has_successful_collection(self, source_id: str, as_of_date: str) -> bool:
        """Return True if a successful or skipped run already exists for this source+date."""
        con = self._connect()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM collection_runs WHERE source_id = ? AND as_of_date = ? AND status IN ('success','warning')",
                [source_id, as_of_date],
            ).fetchone()[0]
            return count > 0
        finally:
            con.close()

    def has_duplicate_sha256(self) -> bool:
        con = self._connect()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM (SELECT sha256 FROM raw_manifest GROUP BY sha256 HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            return count > 0
        finally:
            con.close()

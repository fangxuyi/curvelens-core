from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ccvm.collectors.csv_futures import CSVFuturesCollector
from ccvm.collectors.eia import EIACollector
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore


# ---------------------------------------------------------------------------
# CSVFuturesCollector
# ---------------------------------------------------------------------------

def test_csv_futures_discover_finds_file(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)
    items = collector.discover(date(2024, 1, 2))
    assert len(items) == 1
    assert items[0].identifier == "wti_futures_20240102.csv"


def test_csv_futures_discover_returns_empty_for_unknown_date(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)
    items = collector.discover(date(2020, 1, 1))
    assert items == []


def test_csv_futures_collect_writes_to_raw_store(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)
    result = collector.collect(date(2024, 1, 2))
    assert result["status"] == "success"
    assert result["success"] == 1
    assert result["skipped"] == 0
    assert manifest_db.get_manifest_entry_count() == 1


def test_csv_futures_collect_registers_run(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)
    result = collector.collect(date(2024, 1, 2))
    runs = manifest_db.get_run_history(collector.source_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["run_id"] == result["run_id"]


def test_csv_futures_collect_missing_date_returns_warning(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)
    result = collector.collect(date(2020, 1, 1))
    assert result["status"] == "warning"
    assert result["success"] == 0


# ---------------------------------------------------------------------------
# EIACollector — no network calls; test skipping when no key
# ---------------------------------------------------------------------------

def test_eia_collector_skips_without_api_key(
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    with patch.dict(os.environ, {}, clear=True):
        if "EIA_API_KEY" in os.environ:
            del os.environ["EIA_API_KEY"]
        collector = EIACollector(raw_store, manifest_db, api_key="")
        result = collector.collect(date(2024, 1, 5))
    assert result["status"] == "skipped"
    assert manifest_db.get_manifest_entry_count() == 0


def test_eia_collector_discover_returns_series_set(
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = EIACollector(raw_store, manifest_db, api_key="")
    items = collector.discover(date(2024, 1, 5))
    # EIA weekly petroleum discover fans out to the full series set.
    assert len(items) == 7
    assert any("eia_us_crude_stocks_20240105" in it.identifier for it in items)

"""Profile-driven macro collection from the official FRED API."""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..reference.product import MacroSeriesSpec, get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"


class FREDMacroCollector:
    """Collect configured macro histories without embedding product names."""

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB,
                 api_key: str | None = None) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.product = get_product()
        if self.product.macro is None or self.product.macro.provider != "fred":
            raise ValueError("Active product has no FRED macro capability")
        self.spec = self.product.macro
        self.api_key = api_key or os.environ.get(self.spec.api_key_env, "")
        self.source_id = f"fred_{self.product.key}_macro"

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def _fetch(self, series: MacroSeriesSpec, as_of_date: date) -> bytes:
        response = httpx.get(
            FRED_OBSERVATIONS_URL,
            params={
                "api_key": self.api_key,
                "file_type": "json",
                "series_id": series.series_id,
                "observation_start": (
                    as_of_date - timedelta(days=self.spec.history_days)
                ).isoformat(),
                "observation_end": as_of_date.isoformat(),
                "sort_order": "asc",
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.content

    def collect(self, as_of_date: date) -> dict:
        if not self.api_key:
            logger.warning("%s not set — skipping macro collection", self.spec.api_key_env)
            return {"run_id": None, "status": "skipped", "success": 0,
                    "warning": 0, "failure": 0, "skipped": len(self.spec.series)}

        run_id = str(uuid.uuid4())
        as_of = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of)
        success = failure = skipped = 0
        for series in self.spec.series:
            try:
                content = self._fetch(series, as_of_date)
                sha256 = hashlib.sha256(content).hexdigest()
                if self.manifest_db.sha256_exists_for_date(sha256, as_of):
                    skipped += 1
                    continue
                filename = f"fred_{series.key}_{as_of_date:%Y%m%d}.json"
                source_id = f"{self.source_id}_{series.key}"
                raw_path, sha256, byte_size = self.raw_store.persist(
                    content, source_id, filename, as_of,
                    f"{FRED_OBSERVATIONS_URL}?series_id={series.series_id}",
                    200, "application/json",
                )
                self.manifest_db.insert_manifest_entry({
                    "entry_id": str(uuid.uuid4()), "source_id": source_id,
                    "raw_path": str(raw_path), "sha256": sha256,
                    "byte_size": byte_size, "retrieved_at": datetime.now(timezone.utc),
                    "trade_date": as_of,
                    "source_url": f"{FRED_OBSERVATIONS_URL}?series_id={series.series_id}",
                    "http_status": 200, "content_type": "application/json",
                    "collection_run_id": run_id,
                })
                success += 1
            except Exception as exc:
                logger.error("Failed to collect FRED %s: %s", series.series_id, exc)
                failure += 1

        status = "failed" if failure and not success else "warning" if failure else "success"
        self.manifest_db.complete_run(run_id, status, success, 0, failure, skipped)
        return {"run_id": run_id, "status": status, "success": success,
                "warning": 0, "failure": failure, "skipped": skipped}


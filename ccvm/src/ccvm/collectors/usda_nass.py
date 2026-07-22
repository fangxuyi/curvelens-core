"""USDA NASS Quick Stats collector for profile-selected crop fundamentals."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timezone

import httpx

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)
_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
_CATEGORIES = ("CONDITION", "PROGRESS", "PRODUCTION", "YIELD", "AREA PLANTED", "AREA HARVESTED")


class USDANASSCornCollector:
    """Collect five years of national Corn estimates; skip cleanly without a key."""

    source_id = "usda_nass_corn"

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db

    def _fetch_category(self, key: str, category: str, as_of_date: date) -> list[dict]:
        params = {
            "key": key, "commodity_desc": "CORN", "source_desc": "SURVEY",
            "agg_level_desc": "NATIONAL", "statisticcat_desc": category,
            "year__GE": str(as_of_date.year - 5), "format": "JSON",
        }
        response = httpx.get(_URL, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        return payload.get("data", []) if isinstance(payload, dict) else []

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of)
        key = os.getenv("USDA_NASS_API_KEY")
        if not key:
            note = "USDA_NASS_API_KEY not configured"
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1, notes=note)
            return {"run_id": run_id, "status": "skipped", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1, "detail": note}
        try:
            rows = []
            category_counts = {}
            for category in _CATEGORIES:
                items = self._fetch_category(key, category, as_of_date)
                rows.extend(items)
                category_counts[category] = len(items)
        except Exception as exc:
            note = f"USDA NASS Quick Stats fetch failed: {exc}"
            logger.error(note)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=note)
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0, "detail": note}
        if not rows:
            note = "USDA NASS returned no national Corn observations"
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=note)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0, "detail": note}
        content = json.dumps({
            "as_of_date": as_of, "source_url": _URL,
            "category_counts": category_counts, "data": rows,
        }, indent=2).encode()
        digest = hashlib.sha256(content).hexdigest()
        if self.manifest_db.sha256_exists(digest):
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}
        filename = f"usda_nass_corn_{as_of_date.strftime('%Y%m%d')}.json"
        path, written_digest, size = self.raw_store.persist(
            content=content, source_id=self.source_id, filename=filename,
            trade_date=as_of, source_url=_URL, content_type="application/json",
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()), "source_id": self.source_id,
            "raw_path": str(path), "sha256": written_digest, "byte_size": size,
            "retrieved_at": datetime.now(timezone.utc), "trade_date": as_of,
            "source_url": _URL, "http_status": 200, "content_type": "application/json",
            "collection_run_id": run_id,
        })
        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0,
                                      notes=f"{len(rows)} observations")
        return {"run_id": run_id, "status": "success", "success": 1,
                "warning": 0, "failure": 0, "skipped": 0, "observations": len(rows)}

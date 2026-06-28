from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore
from .base import CollectionItem, RawPayload

logger = logging.getLogger(__name__)

EIA_BASE_URL = "https://api.eia.gov/v2"

# Each entry is one API call → one raw file per trade_date.
# key        : used in filename and to look up spec in fetch()
# endpoint   : path appended to EIA_BASE_URL
# facets     : dict of facet_name → list of values
# length     : rows to fetch (52 weeks ≈ 1 year of weekly history)
SERIES_SPECS: list[dict] = [
    {
        "key": "us_crude_stocks",
        "endpoint": "petroleum/stoc/wstk/data/",
        "facets": {"product": ["EPC0"], "duoarea": ["NUS"]},
        "length": 52,
    },
    {
        "key": "cushing_crude_stocks",
        "endpoint": "petroleum/stoc/wstk/data/",
        "facets": {"product": ["EPC0"], "duoarea": ["YCUOK"]},
        "length": 52,
    },
    {
        "key": "us_crude_imports",
        "endpoint": "petroleum/move/wkly/data/",
        "facets": {"series": ["WCRIMUS2"]},
        "length": 52,
    },
    {
        "key": "us_crude_exports",
        "endpoint": "petroleum/move/wkly/data/",
        "facets": {"series": ["WCREXUS2"]},
        "length": 52,
    },
    {
        "key": "us_refinery_utilization",
        "endpoint": "petroleum/pnp/wiup/data/",
        "facets": {"duoarea": ["NUS"]},
        "length": 52,
    },
    {
        "key": "us_gasoline_stocks",
        "endpoint": "petroleum/stoc/wstk/data/",
        "facets": {"product": ["EPM0"], "duoarea": ["NUS"]},
        "length": 52,
    },
    {
        "key": "us_distillate_stocks",
        "endpoint": "petroleum/stoc/wstk/data/",
        "facets": {"product": ["EPD0"], "duoarea": ["NUS"]},
        "length": 52,
    },
]

_SPEC_BY_KEY: dict[str, dict] = {s["key"]: s for s in SERIES_SPECS}


class EIACollector:
    """Fetches weekly EIA petroleum data series from EIA Open Data API v2.

    One raw JSON file is written per series per trade_date.
    """

    source_id = "eia_api_v2"

    def __init__(
        self,
        raw_store: RawStore,
        manifest_db: ManifestDB,
        api_key: Optional[str] = None,
    ) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.api_key = api_key or os.environ.get("EIA_API_KEY", "")

    def discover(self, as_of_date: date) -> list[CollectionItem]:
        date_str = as_of_date.strftime("%Y%m%d")
        return [
            CollectionItem(
                source_id=self.source_id,
                trade_date=as_of_date.isoformat(),
                identifier=f"eia_{spec['key']}_{date_str}.json",
                metadata={"series_key": spec["key"]},
            )
            for spec in SERIES_SPECS
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def _fetch_raw(self, spec: dict, api_key: str) -> bytes:
        params: dict = {
            "api_key": api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": str(spec["length"]),
        }
        for facet_name, values in spec["facets"].items():
            for i, v in enumerate(values):
                params[f"facets[{facet_name}][]"] = v

        url = f"{EIA_BASE_URL}/{spec['endpoint']}"
        response = httpx.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        return response.content

    def fetch(self, item: CollectionItem) -> RawPayload:
        series_key = item.metadata.get("series_key") or _key_from_identifier(item.identifier)
        spec = _SPEC_BY_KEY[series_key]
        content = self._fetch_raw(spec, self.api_key)
        url = f"{EIA_BASE_URL}/{spec['endpoint']}"
        return RawPayload(
            content=content,
            filename=item.identifier,
            trade_date=item.trade_date,
            source_url=url,
            http_status=200,
            content_type="application/json",
        )

    def collect(self, as_of_date: date) -> dict:
        if not self.api_key:
            logger.warning("EIA_API_KEY not set — skipping EIA collection")
            return {"run_id": None, "status": "skipped", "success": 0, "warning": 0, "failure": 0, "skipped": len(SERIES_SPECS)}

        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        items = self.discover(as_of_date)
        success = warning = failure = skipped = 0

        for item in items:
            series_key = item.metadata.get("series_key", item.identifier)
            try:
                payload = self.fetch(item)
                sha256 = hashlib.sha256(payload.content).hexdigest()

                # Skip only if this exact (sha256, trade_date) pair already registered.
                if self.manifest_db.sha256_exists_for_date(sha256, as_of_str):
                    logger.debug("Skipping %s — already registered for %s", item.identifier, as_of_str)
                    skipped += 1
                    continue

                # If same content exists for a different trade_date, reuse the raw file.
                existing = self.manifest_db.get_entry_by_sha256(sha256)
                if existing:
                    raw_path = existing["raw_path"]
                    byte_size = existing["byte_size"]
                    logger.info("Reusing existing raw file for EIA %s (same weekly release)", series_key)
                else:
                    raw_path, sha256, byte_size = self.raw_store.persist(
                        content=payload.content,
                        source_id=self.source_id,
                        filename=payload.filename,
                        trade_date=as_of_str,
                        source_url=payload.source_url,
                        http_status=payload.http_status,
                        content_type=payload.content_type,
                    )
                    logger.info("Collected EIA %s -> %s", series_key, raw_path)

                self.manifest_db.insert_manifest_entry(
                    {
                        "entry_id": str(uuid.uuid4()),
                        "source_id": self.source_id,
                        "raw_path": str(raw_path),
                        "sha256": sha256,
                        "byte_size": byte_size,
                        "retrieved_at": datetime.now(timezone.utc),
                        "trade_date": as_of_str,
                        "source_url": payload.source_url,
                        "http_status": payload.http_status,
                        "content_type": payload.content_type,
                        "collection_run_id": run_id,
                    }
                )
                success += 1

            except Exception as exc:
                logger.error("Failed to collect EIA %s: %s", series_key, exc)
                failure += 1

        status = "failed" if (failure > 0 and success == 0) else ("warning" if failure > 0 else "success")
        self.manifest_db.complete_run(run_id, status, success, warning, failure, skipped)
        return {
            "run_id": run_id, "status": status,
            "success": success, "warning": warning, "failure": failure, "skipped": skipped,
        }


def _key_from_identifier(identifier: str) -> str:
    """Extract series key from filename like 'eia_{key}_{date}.json'."""
    parts = identifier.removeprefix("eia_").rsplit("_", 1)
    return parts[0] if parts else identifier

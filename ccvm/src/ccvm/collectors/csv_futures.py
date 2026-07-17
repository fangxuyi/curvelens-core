from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore
from .base import CollectionItem, RawPayload

logger = logging.getLogger(__name__)


class CSVFuturesCollector:
    """Tier-0 bootstrap collector for product-named local fixture CSVs."""

    def __init__(
        self,
        fixtures_dir: Path,
        raw_store: RawStore,
        manifest_db: ManifestDB,
    ) -> None:
        self.fixtures_dir = Path(fixtures_dir)
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.product = get_product()
        self.source_id = f"csv_{self.product.key}_futures_bootstrap"

    def discover(self, as_of_date: date) -> list[CollectionItem]:
        date_str = as_of_date.strftime("%Y%m%d")
        pattern = f"{self.product.key}_futures_{date_str}.csv"
        paths = sorted(self.fixtures_dir.glob(pattern))
        return [
            CollectionItem(
                source_id=self.source_id,
                trade_date=as_of_date.isoformat(),
                identifier=p.name,
                metadata={"path": str(p)},
            )
            for p in paths
        ]

    def fetch(self, item: CollectionItem) -> RawPayload:
        file_path = Path(item.metadata["path"])
        content = file_path.read_bytes()
        return RawPayload(
            content=content,
            filename=item.identifier,
            trade_date=item.trade_date,
            source_url=str(file_path),
            content_type="text/csv",
        )

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        items = self.discover(as_of_date)
        success = warning = failure = skipped = 0

        if not items:
            logger.warning(
                "No CSV files found for %s in %s", as_of_date, self.fixtures_dir
            )
            warning += 1
            self.manifest_db.complete_run(
                run_id, "warning", success, warning, failure, skipped,
                notes=f"No files found for {as_of_date}",
            )
            return {
                "run_id": run_id, "status": "warning",
                "success": 0, "warning": 1, "failure": 0, "skipped": 0,
            }

        for item in items:
            try:
                payload = self.fetch(item)
                sha256 = hashlib.sha256(payload.content).hexdigest()

                if self.manifest_db.sha256_exists(sha256):
                    logger.debug("Skipping %s — identical content already in manifest", item.identifier)
                    skipped += 1
                    continue

                raw_path, sha256_written, byte_size = self.raw_store.persist(
                    content=payload.content,
                    source_id=self.source_id,
                    filename=payload.filename,
                    trade_date=as_of_str,
                    source_url=payload.source_url,
                    content_type=payload.content_type,
                )
                self.manifest_db.insert_manifest_entry(
                    {
                        "entry_id": str(uuid.uuid4()),
                        "source_id": self.source_id,
                        "raw_path": str(raw_path),
                        "sha256": sha256_written,
                        "byte_size": byte_size,
                        "retrieved_at": datetime.now(timezone.utc),
                        "trade_date": as_of_str,
                        "source_url": payload.source_url,
                        "collection_run_id": run_id,
                    }
                )
                logger.info("Collected %s -> %s", item.identifier, raw_path)
                success += 1

            except Exception as exc:
                logger.error("Failed to collect %s: %s", item.identifier, exc)
                failure += 1

        if failure > 0 and success == 0:
            status = "failed"
        elif failure > 0:
            status = "warning"
        else:
            status = "success"

        self.manifest_db.complete_run(run_id, status, success, warning, failure, skipped)
        return {
            "run_id": run_id, "status": status,
            "success": success, "warning": warning,
            "failure": failure, "skipped": skipped,
        }

"""
Optional profile-configured context benchmark collector via yfinance.

Fetches the profile's front-continuous benchmark ticker for a trailing window
and stores {date: close} as raw JSON. This is a *context* input, not a
settlement store, so the output is labeled as approximate.

history_context reads the latest raw file via find_raw_benchmark().
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yfinance as yf

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 45  # enough history for spread percentiles to bootstrap


class YFinanceBenchmarkCollector:
    """Tier-1 context collector selected by the active product profile."""

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.spec = get_product().benchmark
        self.source_id = (self.spec.source_id if self.spec else
                          f"yfinance_{get_product().key}_benchmark")

    def fetch_and_parse(self, as_of_date: date) -> dict[str, float]:
        if self.spec is None:
            return {}
        start = as_of_date - timedelta(days=_WINDOW_DAYS)
        end = as_of_date + timedelta(days=2)
        raw = yf.download(self.spec.ticker, start=start, end=end, auto_adjust=True,
                          progress=False, group_by="ticker")
        closes: dict[str, float] = {}
        if raw is None or raw.empty:
            return closes
        df = (raw[self.spec.ticker]
              if self.spec.ticker in getattr(raw.columns, "levels", [[]])[0] else raw)
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            close = row.get("Close")
            if close is not None and close == close:  # not NaN
                closes[d.isoformat()] = round(float(close), 4)
        return closes

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)
        if self.spec is None:
            self.manifest_db.complete_run(
                run_id, "success", 0, 0, 0, 1,
                notes="product has no context benchmark",
            )
            return {"run_id": run_id, "status": "skipped", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        try:
            closes = self.fetch_and_parse(as_of_date)
        except Exception as exc:
            msg = f"yfinance {self.spec.name} fetch failed: {exc}"
            logger.error(msg)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=msg)
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0}

        if not closes:
            msg = f"No {self.spec.name} closes returned"
            logger.warning(msg)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=msg)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        content = json.dumps({"ticker": self.spec.ticker, "closes": closes}, indent=2).encode()
        sha256 = hashlib.sha256(content).hexdigest()
        if self.manifest_db.sha256_exists(sha256):
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        filename = f"{self.spec.filename_prefix}_{as_of_date.strftime('%Y%m%d')}.json"
        raw_path, sha_written, byte_size = self.raw_store.persist(
            content=content, source_id=self.source_id, filename=filename,
            trade_date=as_of_str, source_url=f"yfinance:{self.spec.ticker}",
            content_type="application/json",
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": self.source_id,
            "raw_path": str(raw_path),
            "sha256": sha_written,
            "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": as_of_str,
            "source_url": f"yfinance:{self.spec.ticker}",
            "http_status": None,
            "content_type": "application/json",
            "collection_run_id": run_id,
        })
        logger.info("%s benchmark: %d closes (latest %s) → %s",
                    self.spec.name, len(closes), max(closes), raw_path.name)
        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0,
                                      notes=f"{len(closes)} closes")
        return {"run_id": run_id, "status": "success", "success": 1,
                "warning": 0, "failure": 0, "skipped": 0}


def find_raw_benchmark(data_dir: Path, as_of_date: date) -> Optional[Path]:
    """Latest configured benchmark JSON dated no later than as_of."""
    spec = get_product().benchmark
    if spec is None:
        return None
    base = data_dir / "raw" / spec.source_id
    if not base.exists():
        return None
    target = f"{spec.filename_prefix}_{as_of_date.strftime('%Y%m%d')}.json"
    candidates: list[tuple[str, Path]] = []
    for child in sorted(base.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        for f in child.glob(f"{spec.filename_prefix}_*.json"):
            if f.name <= target:
                candidates.append((f.name, f))
    if not candidates:
        return None
    return max(candidates)[1]


def load_benchmark_closes(data_dir: Path, as_of_date: date) -> dict[str, float]:
    """{date: close} from the latest configured benchmark file, or {}."""
    p = find_raw_benchmark(data_dir, as_of_date)
    if p is None:
        return {}
    try:
        return json.loads(p.read_text()).get("closes", {})
    except (json.JSONDecodeError, ValueError):
        logger.warning("Unreadable benchmark raw file %s", p)
        return {}


# Compatibility aliases for callers written before benchmark configuration.
YFinanceBrentCollector = YFinanceBenchmarkCollector
find_raw_brent = find_raw_benchmark
load_brent_closes = load_benchmark_closes

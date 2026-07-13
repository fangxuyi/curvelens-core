"""
Brent front-month collector via yfinance (B5 — Brent–WTI context).

Fetches BZ=F (front-month continuous Brent) daily closes for a trailing
window and stores {date: close} as raw JSON. This is a *context* input —
the Brent–WTI M1 spread line in the brief — not a settlement store, so the
front-continuous ticker is acceptable (labeled as approximate).

history_context reads the latest raw file via find_raw_brent().
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

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

_TICKER = "BZ=F"
_WINDOW_DAYS = 45  # enough history for spread percentiles to bootstrap


class YFinanceBrentCollector:
    """Tier-1 context collector: Brent front-month closes via yfinance."""

    source_id = "yfinance_brent_front"

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db

    def fetch_and_parse(self, as_of_date: date) -> dict[str, float]:
        start = as_of_date - timedelta(days=_WINDOW_DAYS)
        end = as_of_date + timedelta(days=2)
        raw = yf.download(_TICKER, start=start, end=end, auto_adjust=True,
                          progress=False, group_by="ticker")
        closes: dict[str, float] = {}
        if raw is None or raw.empty:
            return closes
        df = raw[_TICKER] if _TICKER in getattr(raw.columns, "levels", [[]])[0] else raw
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

        try:
            closes = self.fetch_and_parse(as_of_date)
        except Exception as exc:
            msg = f"yfinance Brent fetch failed: {exc}"
            logger.error(msg)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=msg)
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0}

        if not closes:
            msg = "No Brent closes returned"
            logger.warning(msg)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=msg)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        content = json.dumps({"ticker": _TICKER, "closes": closes}, indent=2).encode()
        sha256 = hashlib.sha256(content).hexdigest()
        if self.manifest_db.sha256_exists(sha256):
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        filename = f"brent_front_{as_of_date.strftime('%Y%m%d')}.json"
        raw_path, sha_written, byte_size = self.raw_store.persist(
            content=content, source_id=self.source_id, filename=filename,
            trade_date=as_of_str, source_url=f"yfinance:{_TICKER}",
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
            "source_url": f"yfinance:{_TICKER}",
            "http_status": None,
            "content_type": "application/json",
            "collection_run_id": run_id,
        })
        logger.info("Brent front: %d closes (latest %s) → %s",
                    len(closes), max(closes), raw_path.name)
        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0,
                                      notes=f"{len(closes)} closes")
        return {"run_id": run_id, "status": "success", "success": 1,
                "warning": 0, "failure": 0, "skipped": 0}


def find_raw_brent(data_dir: Path, as_of_date: date) -> Optional[Path]:
    """Latest raw Brent JSON with trade date ≤ as_of (searches newest first)."""
    base = data_dir / "raw" / "yfinance_brent_front"
    if not base.exists():
        return None
    target = f"brent_front_{as_of_date.strftime('%Y%m%d')}.json"
    candidates: list[tuple[str, Path]] = []
    for child in sorted(base.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        for f in child.glob("brent_front_*.json"):
            if f.name <= target:
                candidates.append((f.name, f))
    if not candidates:
        return None
    return max(candidates)[1]


def load_brent_closes(data_dir: Path, as_of_date: date) -> dict[str, float]:
    """{date: close} from the latest raw Brent file, or {}."""
    p = find_raw_brent(data_dir, as_of_date)
    if p is None:
        return {}
    try:
        return json.loads(p.read_text()).get("closes", {})
    except (json.JSONDecodeError, ValueError):
        logger.warning("Unreadable Brent raw file %s", p)
        return {}

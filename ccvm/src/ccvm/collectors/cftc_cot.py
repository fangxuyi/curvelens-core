"""
CFTC Commitments of Traders collector (B3).

Disaggregated Futures-and-Options Combined report via the CFTC Socrata API
(no key required). Which contract is pulled is declared in the product profile
(`cot.contract_market_code`), not coded — WTI = 067651, gold = 088691. A profile
with no `cot` block has no positioning feed and the collector skips.

Each run fetches the trailing 3 years of weekly reports (~156 rows) and
stores them as one raw JSON file; sha-dedup makes unchanged re-runs free.
Positions are as of Tuesday, published Friday 15:30 ET — the brief labels
the lag.

analytics/cot_features.py reads the latest raw file via load_cot_rows().
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

_DATASET = "kh3c-gbw2"   # Disaggregated Futures-and-Options Combined
_FIELDS = ",".join([
    "report_date_as_yyyy_mm_dd",
    "m_money_positions_long_all",
    "m_money_positions_short_all",
    "prod_merc_positions_long",
    "prod_merc_positions_short",
    "open_interest_all",
])
_BACKFILL_YEARS = 3


def _cot_source_id() -> str:
    """Per-product COT source id, e.g. cftc_cot_wti / cftc_cot_gc.

    Deployment-scoped (CCVM_PRODUCT), so raw paths never collide across products
    and WTI's existing `cftc_cot_wti` layout is preserved unchanged.
    """
    return f"cftc_cot_{get_product().key}"


class CFTCCOTCollector:
    """Weekly COT positioning via the Socrata open-data API; contract from profile."""

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.source_id = _cot_source_id()

    def fetch(self, as_of_date: date) -> list[dict]:
        code = get_product().cot_contract_market_code
        if not code:
            return []
        since = (as_of_date - timedelta(days=365 * _BACKFILL_YEARS)).isoformat()
        url = f"https://publicreporting.cftc.gov/resource/{_DATASET}.json"
        params = {
            "cftc_contract_market_code": code,
            "$select": _FIELDS,
            "$where": f"report_date_as_yyyy_mm_dd >= '{since}'",
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": "500",
        }
        resp = httpx.get(url, params=params, timeout=60)
        resp.raise_for_status()
        rows = resp.json()
        # normalize: date-only strings, ints
        out = []
        for r in rows:
            try:
                out.append({
                    "report_date": r["report_date_as_yyyy_mm_dd"][:10],
                    "mm_long": int(r["m_money_positions_long_all"]),
                    "mm_short": int(r["m_money_positions_short_all"]),
                    "prod_long": int(r["prod_merc_positions_long"]),
                    "prod_short": int(r["prod_merc_positions_short"]),
                    "open_interest": int(r["open_interest_all"]),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        try:
            rows = self.fetch(as_of_date)
        except Exception as exc:
            msg = f"CFTC COT fetch failed: {exc}"
            logger.error(msg)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=msg)
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0}

        if not rows:
            msg = "No COT rows returned"
            logger.warning(msg)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=msg)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        product = get_product()
        label = product.cot_contract_label or product.name
        content = json.dumps({"contract": f"{label} ({product.cot_contract_market_code})",
                              "rows": rows}, indent=2).encode()
        sha256 = hashlib.sha256(content).hexdigest()
        if self.manifest_db.sha256_exists(sha256):
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        filename = f"{self.source_id}_{as_of_date.strftime('%Y%m%d')}.json"
        raw_path, sha_written, byte_size = self.raw_store.persist(
            content=content, source_id=self.source_id, filename=filename,
            trade_date=as_of_str,
            source_url=f"https://publicreporting.cftc.gov/resource/{_DATASET}.json",
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
            "source_url": f"https://publicreporting.cftc.gov/resource/{_DATASET}.json",
            "http_status": 200,
            "content_type": "application/json",
            "collection_run_id": run_id,
        })
        logger.info("COT: %d weekly reports (latest %s) → %s",
                    len(rows), rows[-1]["report_date"], raw_path.name)
        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0,
                                      notes=f"{len(rows)} reports")
        return {"run_id": run_id, "status": "success", "success": 1,
                "warning": 0, "failure": 0, "skipped": 0}


def find_raw_cot(data_dir: Path, as_of_date: date) -> Optional[Path]:
    """Latest raw COT JSON dated ≤ as_of (searches newest first)."""
    sid = _cot_source_id()
    base = data_dir / "raw" / sid
    if not base.exists():
        return None
    target = f"{sid}_{as_of_date.strftime('%Y%m%d')}.json"
    candidates = []
    for child in sorted(base.iterdir(), reverse=True):
        if child.is_dir():
            for f in child.glob(f"{sid}_*.json"):
                if f.name <= target:
                    candidates.append((f.name, f))
    return max(candidates)[1] if candidates else None


def load_cot_rows(data_dir: Path, as_of_date: date) -> list[dict]:
    p = find_raw_cot(data_dir, as_of_date)
    if p is None:
        return []
    try:
        return json.loads(p.read_text()).get("rows", [])
    except (json.JSONDecodeError, ValueError):
        logger.warning("Unreadable COT raw file %s", p)
        return []

"""
WTI crude oil options collector via Barchart OnDemand API.

Barchart provides options chains for futures contracts including WTI crude.
Settlement prices are last-trade (not official CME settlement), but Greeks,
IV, volume, and open interest are included — enough for bootstrap analytics.

Setup (one-time):
    1. Register at https://www.barchart.com/ondemand/free-api-key  (instant, free)
    2. export BARCHART_API_KEY="your_key_here"

Free tier limits:
    - 100 API calls per day
    - This collector uses 1 call per underlying contract (front 5 = 5 calls/run)

Upgrade path:
    - Paid tiers remove rate limits and unlock historical end-of-day data
    - Official CME settlement prices require CME DataMine (Tier-2)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

_BASE_URL = "https://marketdata.websol.barchart.com/getOptions.json"

_MONTH_LETTERS = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

# Fields to request from Barchart
_FIELDS = (
    "strike,lastPrice,bidPrice,askPrice,volume,openInterest,"
    "impliedVolatility,delta,gamma,theta,vega,expirationDate,side"
)


def _active_cl_symbols(as_of_date: date, count: int = 5) -> list[tuple[str, str]]:
    """Return (barchart_symbol, delivery_month) for the next `count` active WTI contracts."""
    symbols = []
    for i in range(1, count + 2):
        total = as_of_date.month + i - 1
        m = total % 12 + 1
        y = as_of_date.year + total // 12
        letter = _MONTH_LETTERS[m]
        y2 = str(y)[2:]
        symbols.append((f"CL{letter}{y2}", f"{y:04d}-{m:02d}"))
    return symbols[:count]


def _parse_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _parse_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class BarchartOptionsCollector:
    """
    Tier-1 bootstrap options collector using Barchart OnDemand API.
    Fetches options chains for the front N WTI monthly contracts.
    """

    source_id = "barchart_wti_options"

    def __init__(
        self,
        raw_store: RawStore,
        manifest_db: ManifestDB,
        max_underlying_contracts: int = 5,
    ) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.max_underlying_contracts = max_underlying_contracts
        self.api_key = os.environ.get("BARCHART_API_KEY", "")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _fetch_chain(self, symbol: str) -> bytes:
        params = {
            "apikey": self.api_key,
            "symbol": symbol,
            "fields": _FIELDS,
            "raw": "1",
        }
        with httpx.Client(timeout=30) as client:
            resp = client.get(_BASE_URL, params=params)
            resp.raise_for_status()
            return resp.content

    def parse_chain(self, content: bytes, as_of_date: date,
                    underlying_symbol: str, underlying_delivery_month: str) -> list[dict]:
        """Parse Barchart getOptions JSON into OptionSettlement-compatible dicts."""
        data = json.loads(content)

        if data.get("status", {}).get("code") != 200:
            msg = data.get("status", {}).get("message", "unknown error")
            logger.warning("Barchart API error for %s: %s", underlying_symbol, msg)
            return []

        results = data.get("results") or []
        records: list[dict] = []

        for row in results:
            strike = _parse_float(row.get("strike"))
            if strike is None or strike <= 0:
                continue

            side = str(row.get("side", "")).upper()
            if side == "CALL":
                call_put = "C"
            elif side == "PUT":
                call_put = "P"
            else:
                continue

            last = _parse_float(row.get("lastPrice"))
            if last is None or last < 0:
                continue

            exp_str = row.get("expirationDate", "")
            try:
                option_expiry = date.fromisoformat(exp_str)
            except (ValueError, TypeError):
                logger.debug("Cannot parse expirationDate %r — skipping", exp_str)
                continue

            if option_expiry <= as_of_date:
                continue

            records.append({
                "trade_date": as_of_date.isoformat(),
                "option_expiry": option_expiry.isoformat(),
                "underlying_contract": underlying_symbol,
                "underlying_delivery_month": underlying_delivery_month,
                "strike": strike,
                "call_put": call_put,
                "settlement": last,
                "bid": _parse_float(row.get("bidPrice")),
                "ask": _parse_float(row.get("askPrice")),
                "volume": _parse_int(row.get("volume")),
                "open_interest": _parse_int(row.get("openInterest")),
                "implied_volatility": _parse_float(row.get("impliedVolatility")),
                "delta": _parse_float(row.get("delta")),
                "gamma": _parse_float(row.get("gamma")),
                "theta": _parse_float(row.get("theta")),
                "vega": _parse_float(row.get("vega")),
                "exercise_style": "American",
                "settlement_style": "Futures",
                "contract_multiplier": 1000,
                "source_id": self.source_id,
                "price_note": "last_trade_not_official_cme_settlement",
            })

        return records

    def collect(self, as_of_date: date) -> dict:
        if not self.api_key:
            logger.warning(
                "BARCHART_API_KEY not set — skipping options collection.\n"
                "  Register free at: https://www.barchart.com/ondemand/free-api-key\n"
                "  Then: export BARCHART_API_KEY=your_key"
            )
            return {"run_id": None, "status": "skipped",
                    "notes": "BARCHART_API_KEY not set"}

        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        underlyings = _active_cl_symbols(as_of_date, self.max_underlying_contracts)
        all_records: list[dict] = []
        errors: list[str] = []

        for symbol, delivery_month in underlyings:
            try:
                content = self._fetch_chain(symbol)
                records = self.parse_chain(content, as_of_date, symbol, delivery_month)
                all_records.extend(records)
                logger.info("  %s: %d option records", symbol, len(records))
            except httpx.HTTPStatusError as exc:
                msg = f"{symbol}: HTTP {exc.response.status_code}"
                logger.error(msg)
                errors.append(msg)
            except Exception as exc:
                msg = f"{symbol}: {exc}"
                logger.error(msg)
                errors.append(msg)

        if not all_records and errors:
            self.manifest_db.complete_run(run_id, "failed", 0, 0, len(errors), 0,
                                          notes="; ".join(errors))
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": len(errors), "skipped": 0}

        if not all_records:
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0,
                                          notes="No option records returned")
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        filename = f"barchart_cl_options_{as_of_date.strftime('%Y%m%d')}.json"

        # Idempotency: skip if a successful run already exists for this source+date.
        if self.manifest_db.has_successful_collection(self.source_id, as_of_str):
            logger.info("Skipping %s — already collected for %s", filename, as_of_str)
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        content = json.dumps({
            "source": self.source_id,
            "trade_date": as_of_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "underlying_contracts": [s for s, _ in underlyings],
            "record_count": len(all_records),
            "caveat": "last_trade_prices_not_official_cme_settlements",
            "settlements": all_records,
        }, indent=2).encode()

        raw_path, sha256_written, byte_size = self.raw_store.persist(
            content=content,
            source_id=self.source_id,
            filename=filename,
            trade_date=as_of_str,
            source_url=_BASE_URL,
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": self.source_id,
            "raw_path": str(raw_path),
            "sha256": sha256_written,
            "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": as_of_str,
            "source_url": _BASE_URL,
            "collection_run_id": run_id,
        })

        status = "warning" if errors else "success"
        notes = "; ".join(errors) if errors else None
        self.manifest_db.complete_run(run_id, status, 1 if not errors else 0,
                                      1 if errors else 0, 0, 0, notes=notes)
        logger.info("Stored %d WTI option records for %s -> %s",
                    len(all_records), as_of_date, raw_path)
        return {"run_id": run_id, "status": status, "success": 1, "warning": len(errors),
                "failure": 0, "skipped": 0, "records": len(all_records)}

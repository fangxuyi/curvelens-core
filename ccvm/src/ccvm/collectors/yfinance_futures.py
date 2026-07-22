"""
Product-profile-driven futures curve collector via yfinance.

Downloads individual contract tickers (e.g. CLQ26.NYM) for the next N months.
Settlement price = daily Close. Open interest is not available via yfinance.
Source tier: Tier-1 public bootstrap (delayed ~15 min intraday; EOD is settlement).
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

def _active_contracts(as_of_date: date, num_months: int | None = None) -> list[tuple[str, str, str]]:
    """
    Return (yf_ticker, contract_code, delivery_month) for the next N listed months.
    The product calendar excludes expired delivery months. Prefix/suffix and
    listed months come from the profile: WTI → CLQ26.NYM, Corn → ZCU26.CBT.
    """
    p = get_product()
    depth = num_months if num_months is not None else p.futures_depth
    contracts = []
    # Start with the current delivery month and let the product calendar remove
    # expired contracts. This matters for grains near delivery-month boundaries.
    offset = 0
    i = 0
    max_scan = max(depth * 12 + 12, 24)
    while len(contracts) < depth and i < max_scan:
        total = as_of_date.month + offset + i - 1
        month = total % 12 + 1
        year = as_of_date.year + total // 12
        i += 1
        if month not in p.listed_futures_months:
            continue
        if p.calendar.futures_last_trade_date(year, month) < as_of_date:
            continue
        letter = p.month_letters[month]
        year_2d = str(year)[2:]
        code = f"{p.futures_prefix}{letter}{year_2d}"
        contracts.append((
            f"{code}{p.yfinance_contract_suffix}",
            code,
            f"{year:04d}-{month:02d}",
        ))
    return contracts


class YFinanceFuturesCollector:
    """
    Tier-1 bootstrap futures collector using yfinance.
    Replaces direct CME HTTP scraping (which requires licensed access).
    """

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB,
                 num_months: int | None = None) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        product = get_product()
        self.num_months = num_months if num_months is not None else product.futures_depth
        self.source_id = f"yfinance_{product.key}_futures"

    def fetch_and_parse(self, as_of_date: date) -> list[dict]:
        contracts = _active_contracts(as_of_date, self.num_months)
        tickers = [c[0] for c in contracts]

        # Download window: a few extra days to handle weekends/holidays
        start = as_of_date - timedelta(days=5)
        end = as_of_date + timedelta(days=2)

        p = get_product()
        logger.info("Downloading %d %s contract tickers from yfinance...",
                    len(tickers), p.display_name)
        raw = yf.download(tickers, start=start, end=end, auto_adjust=True,
                          progress=False, group_by="ticker")

        records: list[dict] = []
        for ticker, contract_code, delivery_month in contracts:
            try:
                # yfinance groups by ticker when multiple tickers requested
                if len(tickers) == 1:
                    df = raw
                else:
                    df = raw[ticker] if ticker in raw.columns.get_level_values(0) else pd.DataFrame()

                if df.empty:
                    logger.debug("No data for %s", ticker)
                    continue

                # Find the row for as_of_date (index is DatetimeIndex, tz-aware or naive)
                idx = df.index
                if hasattr(idx, "tz") and idx.tz is not None:
                    target = pd.Timestamp(as_of_date).tz_localize(idx.tz)
                else:
                    target = pd.Timestamp(as_of_date)

                if target not in idx:
                    # Fall back: find the most recent row on or before as_of_date
                    before = df[df.index.date <= as_of_date]
                    if before.empty:
                        logger.debug("No row on or before %s for %s", as_of_date, ticker)
                        continue
                    row = before.iloc[-1]
                    row_date = before.index[-1].date()
                    if row_date != as_of_date:
                        logger.debug("Nearest date for %s is %s, not %s — skipping",
                                     ticker, row_date, as_of_date)
                        continue
                else:
                    row = df.loc[target]

                close = row.get("Close", row.get("close"))
                volume = row.get("Volume", row.get("volume"))

                if pd.isna(close) or float(close) <= 0:
                    continue

                vol_int: int | None = None
                if volume is not None and not pd.isna(volume):
                    vol_int = int(float(volume))

                settlement = float(close) * p.futures_price_scale
                records.append({
                    "trade_date": as_of_date.isoformat(),
                    "exchange": p.exchange,
                    "product": p.product_code,
                    "contract_code": contract_code,
                    "delivery_month": delivery_month,
                    "settlement": round(settlement, 6),
                    "volume": vol_int,
                    "open_interest": None,
                    "currency": p.currency,
                    "price_unit": p.price_unit,
                    "source_id": self.source_id,
                })
                logger.info("  %s  delivery=%s  settle=%.2f  vol=%s",
                            contract_code, delivery_month, settlement, vol_int)

            except Exception as exc:
                logger.warning("Error processing %s: %s", ticker, exc)
                continue

        return records

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)
        filename = f"yf_{get_product().key}_futures_{as_of_date.strftime('%Y%m%d')}.json"

        try:
            records = self.fetch_and_parse(as_of_date)
        except Exception as exc:
            logger.error("Failed to fetch yfinance futures for %s: %s", as_of_date, exc)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=str(exc))
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0}

        if not records:
            note = f"No futures data for {as_of_date} — weekend, holiday, or market closed"
            logger.warning(note)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=note)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        content = json.dumps({
            "source": self.source_id,
            "trade_date": as_of_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "contract_count": len(records),
            "settlements": records,
        }, indent=2).encode()

        sha256 = hashlib.sha256(content).hexdigest()
        if self.manifest_db.sha256_exists(sha256):
            logger.info("Skipping %s — identical content already stored", filename)
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        raw_path, sha256_written, byte_size = self.raw_store.persist(
            content=content,
            source_id=self.source_id,
            filename=filename,
            trade_date=as_of_str,
            source_url="yfinance (Yahoo Finance delayed feed)",
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": self.source_id,
            "raw_path": str(raw_path),
            "sha256": sha256_written,
            "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": as_of_str,
            "source_url": "yfinance",
            "collection_run_id": run_id,
        })
        logger.info("Stored %d %s contracts for %s -> %s",
                    len(records), get_product().display_name, as_of_date, raw_path)
        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0)
        return {"run_id": run_id, "status": "success", "success": 1,
                "warning": 0, "failure": 0, "skipped": 0,
                "contracts": len(records)}

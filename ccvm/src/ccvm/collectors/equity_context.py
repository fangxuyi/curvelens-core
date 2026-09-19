"""Collect public equity-index context without treating proxies as settlements.

The exchange futures/options handoff remains the authoritative market layer.
This optional capability adds:

* an ETF cash proxy and eleven U.S. sector ETF proxies (Yahoo Finance);
* scheduled earnings for a bounded company watchlist (Alpha Vantage); and
* recent material company filings (SEC EDGAR submissions).

No model API is called. Missing optional credentials produce explicit
limitations rather than fabricated or silently substituted observations.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import yfinance as yf

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_MATERIAL_FORMS = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}


class EquityContextCollector:
    """Build one dated, reproducible context snapshot for an equity index."""

    def __init__(
        self, raw_store: RawStore, manifest_db: ManifestDB,
        *, earnings_api_key: str | None = None,
        sec_user_agent: str | None = None,
    ) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.product = get_product()
        self.spec = self.product.equity_context
        if self.spec is None:
            raise ValueError(
                f"Product {self.product.key!r} has no equity_context capability"
            )
        self.earnings_api_key = (
            earnings_api_key
            if earnings_api_key is not None
            else os.environ.get(self.spec.earnings_api_key_env, "")
        )
        self.sec_user_agent = (
            sec_user_agent
            if sec_user_agent is not None
            else os.environ.get(self.spec.sec_user_agent_env, "")
        )
        self.source_id = f"equity_context_{self.product.key}"

    def _market_context(self, as_of_date: date) -> tuple[dict, list[dict]]:
        tickers = [self.spec.proxy_ticker, *(
            ticker for ticker, _ in self.spec.sector_proxies
        )]
        start = as_of_date - timedelta(days=self.spec.history_days)
        end = as_of_date + timedelta(days=2)
        frame = yf.download(
            tickers, start=start, end=end, auto_adjust=True,
            progress=False, group_by="ticker", threads=False,
        )
        results = {}
        for ticker in tickers:
            try:
                series = frame[ticker]["Close"] if len(tickers) > 1 else frame["Close"]
                values = [
                    (idx.date().isoformat(), float(value))
                    for idx, value in series.items()
                    if value == value and idx.date() <= as_of_date
                ]
            except (KeyError, TypeError, AttributeError):
                values = []
            if not values:
                continue
            latest_date, latest = values[-1]
            prior = values[-2][1] if len(values) >= 2 else None
            results[ticker] = {
                "ticker": ticker,
                "observation_date": latest_date,
                "close": latest,
                "return_1d": (
                    latest / prior - 1.0 if prior not in (None, 0) else None
                ),
                "source": "Yahoo Finance adjusted close (context proxy)",
            }
        proxy = results.get(self.spec.proxy_ticker, {
            "ticker": self.spec.proxy_ticker,
            "status": "unavailable",
        })
        proxy["name"] = self.spec.proxy_name
        sectors = []
        for ticker, name in self.spec.sector_proxies:
            if ticker in results:
                sectors.append({**results[ticker], "name": name})
        sectors.sort(
            key=lambda item: (
                item.get("return_1d") is not None,
                item.get("return_1d") or float("-inf"),
            ),
            reverse=True,
        )
        for rank, item in enumerate(sectors, 1):
            item["rank"] = rank
        return proxy, sectors

    def _earnings(self, as_of_date: date) -> list[dict]:
        if not self.earnings_api_key:
            return []
        response = httpx.get(
            _ALPHA_VANTAGE_URL,
            params={
                "function": "EARNINGS_CALENDAR",
                "horizon": "3month",
                "apikey": self.earnings_api_key,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        if response.text.lstrip().startswith("{"):
            try:
                detail = response.json()
            except ValueError:
                detail = {"error": "unexpected JSON response"}
            raise ValueError(f"Alpha Vantage earnings response: {detail}")
        names = {
            company.ticker: company.name
            for company in self.spec.company_watchlist
        }
        horizon = as_of_date + timedelta(days=14)
        rows = []
        for row in csv.DictReader(io.StringIO(response.text)):
            ticker = str(row.get("symbol", "")).upper()
            try:
                report_date = date.fromisoformat(str(row.get("reportDate", "")))
            except ValueError:
                continue
            if ticker in names and as_of_date <= report_date <= horizon:
                rows.append({
                    "ticker": ticker,
                    "company": names[ticker],
                    "report_date": report_date.isoformat(),
                    "fiscal_date_ending": row.get("fiscalDateEnding"),
                    "estimate": row.get("estimate") or None,
                    "currency": row.get("currency") or None,
                    "source": "Alpha Vantage EARNINGS_CALENDAR",
                    "source_url": _ALPHA_VANTAGE_URL,
                })
        return sorted(rows, key=lambda row: (row["report_date"], row["ticker"]))

    def _filings(self, as_of_date: date) -> list[dict]:
        if not self.sec_user_agent:
            return []
        headers = {
            "User-Agent": self.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        earliest = as_of_date - timedelta(days=5)
        rows = []
        for company in self.spec.company_watchlist:
            response = httpx.get(
                _SEC_SUBMISSIONS_URL.format(cik=company.cik),
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            recent = response.json().get("filings", {}).get("recent", {})
            for index, form in enumerate(recent.get("form", [])):
                if form not in _MATERIAL_FORMS:
                    continue
                try:
                    filing_date = date.fromisoformat(recent["filingDate"][index])
                except (IndexError, KeyError, ValueError):
                    continue
                if not earliest <= filing_date <= as_of_date:
                    continue
                accession = recent["accessionNumber"][index]
                primary = recent["primaryDocument"][index]
                accession_path = accession.replace("-", "")
                cik_path = str(int(company.cik))
                rows.append({
                    "ticker": company.ticker,
                    "company": company.name,
                    "form": form,
                    "filing_date": filing_date.isoformat(),
                    "report_date": (
                        recent.get("reportDate", [])[index]
                        if index < len(recent.get("reportDate", []))
                        else None
                    ),
                    "accession_number": accession,
                    "source": "SEC EDGAR submissions",
                    "source_url": (
                        f"https://www.sec.gov/Archives/edgar/data/{cik_path}/"
                        f"{accession_path}/{primary}"
                    ),
                })
        return sorted(
            rows, key=lambda row: (row["filing_date"], row["ticker"]),
            reverse=True,
        )

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        trade_date = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, trade_date)
        limitations = [
            (
                "Sector returns use liquid U.S. sector ETFs as broad-market "
                "proxies, not licensed constituent-weight attribution."
            ),
            (
                f"{self.spec.proxy_ticker} is a contextual adjusted close and "
                "is not synchronized with the official futures settlement."
            ),
        ]
        try:
            proxy, sectors = self._market_context(as_of_date)
        except Exception as exc:
            logger.warning("Equity proxy collection failed: %s", exc)
            proxy, sectors = (
                {"ticker": self.spec.proxy_ticker, "status": "unavailable"}, []
            )
            limitations.append(f"ETF context unavailable: {exc}")
        try:
            earnings = self._earnings(as_of_date)
        except Exception as exc:
            logger.warning("Earnings calendar collection failed: %s", exc)
            earnings = []
            limitations.append(f"Earnings calendar unavailable: {exc}")
        if not self.earnings_api_key:
            limitations.append(
                f"{self.spec.earnings_api_key_env} is not set; upcoming "
                "earnings were not collected."
            )
        try:
            filings = self._filings(as_of_date)
        except Exception as exc:
            logger.warning("SEC filing collection failed: %s", exc)
            filings = []
            limitations.append(f"SEC filing context unavailable: {exc}")
        if not self.sec_user_agent:
            limitations.append(
                f"{self.spec.sec_user_agent_env} is not set; recent SEC "
                "filings were not collected."
            )
        payload = {
            "trade_date": trade_date,
            "product": self.product.key,
            "index_proxy": proxy,
            "sector_proxies": sectors,
            "top_sectors": sectors[:3],
            "bottom_sectors": list(reversed(sectors[-3:])),
            "upcoming_earnings": earnings,
            "recent_material_filings": filings,
            "watchlist": [
                {
                    "ticker": company.ticker, "name": company.name,
                    "cik": company.cik,
                }
                for company in self.spec.company_watchlist
            ],
            "limitations": limitations,
        }
        content = json.dumps(payload, indent=2).encode()
        digest = hashlib.sha256(content).hexdigest()
        if self.manifest_db.sha256_exists_for_date(digest, trade_date):
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {
                "run_id": run_id, "status": "success", "success": 0,
                "warning": 0, "failure": 0, "skipped": 1,
            }
        path, sha256, byte_size = self.raw_store.persist(
            content, self.source_id,
            f"{self.product.key}_equity_context_{as_of_date:%Y%m%d}.json",
            trade_date, "composite:yfinance+alphavantage+sec-edgar",
            content_type="application/json",
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()), "source_id": self.source_id,
            "raw_path": str(path), "sha256": sha256, "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": trade_date,
            "source_url": "composite:yfinance+alphavantage+sec-edgar",
            "content_type": "application/json", "collection_run_id": run_id,
        })
        status = "success" if sectors else "warning"
        self.manifest_db.complete_run(
            run_id, status, 1 if sectors else 0, 0 if sectors else 1, 0, 0,
            notes=f"{len(sectors)} sectors, {len(earnings)} earnings, {len(filings)} filings",
        )
        return {
            "run_id": run_id, "status": status,
            "success": 1 if sectors else 0, "warning": 0 if sectors else 1,
            "failure": 0, "skipped": 0, "path": str(path),
        }


def load_equity_context(data_dir: Path, product_key: str, trade_date: str) -> dict | None:
    """Load the newest exact-date context snapshot from the raw store."""
    base = Path(data_dir) / "raw" / f"equity_context_{product_key}"
    if not base.exists():
        return None
    suffix = trade_date.replace("-", "")
    candidates = list(base.glob(f"*/{product_key}_equity_context_{suffix}.json"))
    if not candidates:
        return None
    try:
        return json.loads(max(candidates, key=lambda path: path.stat().st_mtime).read_text())
    except (OSError, json.JSONDecodeError):
        return None

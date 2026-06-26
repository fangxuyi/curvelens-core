"""
WTI crude oil options collector via E*TRADE Developer API.

Uses USO (United States Oil Fund) equity options as a liquid, accessible proxy
for WTI crude oil price exposure.  USO tracks WTI front-month spot and is
highly correlated (>0.95) with WTI crude, making its options a practical
stand-in for building volatility surfaces and Greeks analysis.

Limitation: these are EQUITY options on an ETF, NOT CME LO futures options.
Exercise style differs (equity options are American, not futures-settled) and
the underlying price scale is different (~1/10 of WTI barrel price).  A
`price_note` field in every record flags this clearly.

For official CME WTI futures options you would need CME DataMine (paid) or an
FCM with an explicit futures options API (e.g., IBKR TWS or CQG).

Setup (one-time):
    1. Log into https://us.etrade.com/etx/hw/accountspref#/devpref
       (Account > Preferences > Developer > create "ccvm" app)
       — Get CONSUMER_KEY and CONSUMER_SECRET
    2. Run the authorization helper once to get your access tokens:
           python scripts/auth_etrade.py
       This opens a browser URL, you click "Accept", paste the verifier code back.
       Tokens are saved to  ~/.ccvm/etrade_tokens.json  (auto-reloaded by this collector).
    3. Tokens expire at midnight ET; rerun auth_etrade.py after that.
       (E*TRADE's renewAccessToken endpoint is called automatically if the token
       was issued in the same calendar day.)

Environment variables (alternative to token file):
    ETRADE_CONSUMER_KEY
    ETRADE_CONSUMER_SECRET
    ETRADE_ACCESS_TOKEN
    ETRADE_ACCESS_TOKEN_SECRET
    ETRADE_SANDBOX=1   # optional — uses paper/sandbox environment

API reference: https://developer.etrade.com/getting-started/developer-guides
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

_LIVE_BASE = "https://api.etrade.com"
_SANDBOX_BASE = "https://apisb.etrade.com"

# USO: United States Oil Fund LP  — highly liquid oil-tracking ETF
# options on this are available via standard equity options API
_OIL_PROXY_SYMBOL = "USO"

_TOKEN_FILE = Path.home() / ".ccvm" / "etrade_tokens.json"


# ---------------------------------------------------------------------------
# OAuth 1.0a (pure stdlib — no requests-oauthlib needed)
# ---------------------------------------------------------------------------

def _pct_encode(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _oauth1_auth_header(
    url: str,
    method: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    query_params: dict | None = None,
) -> str:
    """Compute OAuth 1.0a Authorization header using HMAC-SHA1."""
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }

    # All params that go into the signature base string
    all_params: dict[str, str] = {}
    all_params.update(query_params or {})
    all_params.update(oauth_params)

    # Percent-encode keys and values, then sort
    encoded = sorted((_pct_encode(k), _pct_encode(v)) for k, v in all_params.items())
    param_string = "&".join(f"{k}={v}" for k, v in encoded)

    # Signature base string
    sig_base = f"{method.upper()}&{_pct_encode(url)}&{_pct_encode(param_string)}"

    # Signing key
    signing_key = f"{_pct_encode(consumer_secret)}&{_pct_encode(token_secret)}"

    # HMAC-SHA1
    digest = hmac.new(signing_key.encode("ascii"), sig_base.encode("ascii"), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()
    oauth_params["oauth_signature"] = signature

    # Build header string — realm="" must come first (E*TRADE requirement)
    header_parts = [('realm', '')]
    header_parts += sorted((k, v) for k, v in oauth_params.items())
    parts = ", ".join(f'{k}="{_pct_encode(v)}"' for k, v in header_parts)
    return f"OAuth {parts}"


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

def _load_tokens() -> dict:
    """Load tokens from env vars (preferred) or ~/.ccvm/etrade_tokens.json."""
    ck = os.environ.get("ETRADE_CONSUMER_KEY", "")
    cs = os.environ.get("ETRADE_CONSUMER_SECRET", "")
    at = os.environ.get("ETRADE_ACCESS_TOKEN", "")
    ats = os.environ.get("ETRADE_ACCESS_TOKEN_SECRET", "")

    if ck and cs and at and ats:
        return {"consumer_key": ck, "consumer_secret": cs,
                "access_token": at, "access_token_secret": ats}

    if _TOKEN_FILE.exists():
        try:
            data = json.loads(_TOKEN_FILE.read_text())
            # env vars override individual fields
            data["consumer_key"] = ck or data.get("consumer_key", "")
            data["consumer_secret"] = cs or data.get("consumer_secret", "")
            data["access_token"] = at or data.get("access_token", "")
            data["access_token_secret"] = ats or data.get("access_token_secret", "")
            return data
        except Exception as exc:
            logger.warning("Failed to read %s: %s", _TOKEN_FILE, exc)

    return {"consumer_key": ck, "consumer_secret": cs,
            "access_token": at, "access_token_secret": ats}


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class ETradeOptionsCollector:
    """
    Collects USO equity options from E*TRADE as a proxy for WTI crude oil options.
    Front N monthly expirations, all strikes, calls + puts.
    """

    source_id = "etrade_uso_options"

    def __init__(
        self,
        raw_store: RawStore,
        manifest_db: ManifestDB,
        max_expiries: int = 5,
        use_sandbox: bool | None = None,
    ) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.max_expiries = max_expiries
        use_sb = use_sandbox if use_sandbox is not None else bool(os.environ.get("ETRADE_SANDBOX"))
        self.base_url = _SANDBOX_BASE if use_sb else _LIVE_BASE
        self._tokens = _load_tokens()

    def _headers(self, url: str, params: dict | None = None) -> dict:
        t = self._tokens
        auth = _oauth1_auth_header(
            url=url,
            method="GET",
            consumer_key=t["consumer_key"],
            consumer_secret=t["consumer_secret"],
            token=t["access_token"],
            token_secret=t["access_token_secret"],
            query_params=params,
        )
        return {"Authorization": auth, "Accept": "application/json"}

    def _try_renew_token(self) -> None:
        """Call renewAccessToken — valid only in same calendar day as original auth."""
        url = f"{self.base_url}/oauth/renewAccessToken"
        t = self._tokens
        auth = _oauth1_auth_header(
            url=url, method="GET",
            consumer_key=t["consumer_key"], consumer_secret=t["consumer_secret"],
            token=t["access_token"], token_secret=t["access_token_secret"],
        )
        try:
            resp = httpx.get(url, headers={"Authorization": auth, "Accept": "application/json"}, timeout=10)
            if resp.status_code == 200:
                # parse new token from response (form-encoded: oauth_token=...&oauth_token_secret=...)
                new_params = dict(urllib.parse.parse_qsl(resp.text))
                if "oauth_token" in new_params:
                    self._tokens["access_token"] = new_params["oauth_token"]
                    self._tokens["access_token_secret"] = new_params.get("oauth_token_secret", t["access_token_secret"])
                    logger.info("E*TRADE access token renewed successfully")
        except Exception as exc:
            logger.debug("Token renewal attempt failed: %s", exc)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _fetch_option_chain(self, expiry_year: int, expiry_month: int) -> dict:
        """GET /v1/market/optionchains for USO for a specific expiration month."""
        url = f"{self.base_url}/v1/market/optionchains"
        params = {
            "symbol": _OIL_PROXY_SYMBOL,
            "expiryYear": str(expiry_year),
            "expiryMonth": str(expiry_month),
            "chainType": "CALLPUT",
            "optionCategory": "STANDARD",
            "skipAdjusted": "true",
            # No noOfStrikes — fetches the full exchange-listed strike range
        }
        resp = httpx.get(
            url,
            params=params,
            headers=self._headers(url, params),
            timeout=30,
        )
        if resp.status_code == 401:
            # Try renewing the token once, then retry
            self._try_renew_token()
            resp = httpx.get(
                url,
                params=params,
                headers=self._headers(url, params),
                timeout=30,
            )
        resp.raise_for_status()
        return resp.json()

    def parse_chain(self, data: dict, as_of_date: date) -> list[dict]:
        """
        Parse E*TRADE OptionChainResponse into OptionSettlement-compatible records.

        E*TRADE response structure:
        {
          "OptionChainResponse": {
            "OptionPair": [
              {
                "Call": {"strikePrice": 75.0, "bid": 1.5, "ask": 1.6, ...},
                "Put":  {"strikePrice": 75.0, "bid": 0.4, "ask": 0.5, ...}
              }
            ],
            "timeStamp": 1719244800,
            "SelectedED": {
              "year": 2026, "month": 7, "day": 18, "expiryType": "MONTHLY"
            }
          }
        }
        """
        chain_resp = data.get("OptionChainResponse", {})
        selected_ed = chain_resp.get("SelectedED", {})
        exp_year = selected_ed.get("year")
        exp_month = selected_ed.get("month")
        exp_day = selected_ed.get("day")

        if not (exp_year and exp_month and exp_day):
            return []

        try:
            option_expiry = date(int(exp_year), int(exp_month), int(exp_day))
        except (ValueError, TypeError):
            return []

        if option_expiry <= as_of_date:
            return []

        exp_str = option_expiry.isoformat()
        records: list[dict] = []

        for pair in chain_resp.get("OptionPair", []):
            for side_key, cp_label in (("Call", "C"), ("Put", "P")):
                opt = pair.get(side_key)
                if not opt:
                    continue

                strike = _f(opt.get("strikePrice"))
                if strike is None or strike <= 0:
                    continue

                last = _f(opt.get("lastPrice"))
                bid = _f(opt.get("bid"))
                ask = _f(opt.get("ask"))

                # Use mid-price if no last trade recorded
                if (last is None or last == 0) and bid is not None and ask is not None:
                    last = round((bid + ask) / 2.0, 4)
                if last is None:
                    continue

                greeks = opt.get("OptionGreeks") or {}
                records.append({
                    "trade_date": as_of_date.isoformat(),
                    "option_expiry": exp_str,
                    "option_symbol": opt.get("optionSymbol", ""),
                    "underlying_contract": _OIL_PROXY_SYMBOL,
                    "underlying_delivery_month": f"{exp_year:04d}-{exp_month:02d}",
                    "strike": strike,
                    "call_put": cp_label,
                    "settlement": last,
                    "bid": bid,
                    "ask": ask,
                    "volume": _i(opt.get("volume")),
                    "open_interest": _i(opt.get("openInterest")),
                    "implied_volatility": _f(greeks.get("iv") or opt.get("impliedVolatility")),
                    "delta": _f(greeks.get("delta")),
                    "gamma": _f(greeks.get("gamma")),
                    "theta": _f(greeks.get("theta")),
                    "vega": _f(greeks.get("vega")),
                    "exercise_style": "American",
                    "settlement_style": "Equity_ETF",
                    "contract_multiplier": 100,
                    "source_id": self.source_id,
                    "price_note": "USO_equity_option_proxy_not_CL_futures_option",
                })

        return records

    def collect(self, as_of_date: date) -> dict:
        t = self._tokens
        if not (t.get("consumer_key") and t.get("access_token")):
            logger.warning(
                "E*TRADE credentials not configured.\n"
                "  Step 1: Create an app at https://us.etrade.com/etx/hw/accountspref#/devpref\n"
                "  Step 2: Run:  python scripts/auth_etrade.py\n"
                "  Or set env vars: ETRADE_CONSUMER_KEY, ETRADE_CONSUMER_SECRET,\n"
                "                   ETRADE_ACCESS_TOKEN, ETRADE_ACCESS_TOKEN_SECRET"
            )
            return {"run_id": None, "status": "skipped",
                    "notes": "E*TRADE credentials not configured"}

        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        if self.manifest_db.has_successful_collection(self.source_id, as_of_str):
            logger.info("Already collected %s for %s — skipping", self.source_id, as_of_str)
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        all_records: list[dict] = []
        errors: list[str] = []

        # Collect front N monthly expirations starting from next month
        m = as_of_date.month
        y = as_of_date.year
        fetched = 0
        attempts = 0

        while fetched < self.max_expiries and attempts < self.max_expiries + 2:
            attempts += 1
            m += 1
            if m > 12:
                m = 1
                y += 1

            try:
                data = self._fetch_option_chain(y, m)
                records = self.parse_chain(data, as_of_date)
                if records:
                    all_records.extend(records)
                    fetched += 1
                    logger.info("  USO %d-%02d: %d option records", y, m, len(records))
                else:
                    logger.debug("  USO %d-%02d: no records (no expiry or expired)", y, m)
            except httpx.HTTPStatusError as exc:
                msg = f"USO {y}-{m:02d}: HTTP {exc.response.status_code}"
                logger.error(msg)
                errors.append(msg)
            except Exception as exc:
                msg = f"USO {y}-{m:02d}: {exc}"
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

        filename = f"etrade_uso_options_{as_of_date.strftime('%Y%m%d')}.json"
        content = json.dumps({
            "source": self.source_id,
            "underlying": _OIL_PROXY_SYMBOL,
            "trade_date": as_of_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "expiry_count": fetched,
            "record_count": len(all_records),
            "caveat": (
                "USO_equity_options_are_a_proxy_for_WTI_crude_oil. "
                "Strike prices are in USD per USO share (not per WTI barrel). "
                "Not official CME LO futures option settlements."
            ),
            "settlements": all_records,
        }, indent=2).encode()

        raw_path, sha256, byte_size = self.raw_store.persist(
            content=content,
            source_id=self.source_id,
            filename=filename,
            trade_date=as_of_str,
            source_url=f"{self.base_url}/v1/market/optionchains?symbol={_OIL_PROXY_SYMBOL}",
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": self.source_id,
            "raw_path": str(raw_path),
            "sha256": sha256,
            "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": as_of_str,
            "source_url": f"{self.base_url}/v1/market/optionchains?symbol={_OIL_PROXY_SYMBOL}",
            "collection_run_id": run_id,
        })

        status = "warning" if errors else "success"
        self.manifest_db.complete_run(run_id, status, 1 if not errors else 0,
                                      1 if errors else 0, 0, 0,
                                      notes="; ".join(errors) if errors else None)
        logger.info("Stored %d USO option records for %s → %s",
                    len(all_records), as_of_date, raw_path)
        return {"run_id": run_id, "status": status, "success": 1 if not errors else 0,
                "warning": int(bool(errors)), "failure": 0, "skipped": 0,
                "records": len(all_records)}

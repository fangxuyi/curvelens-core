"""
CME Daily Bulletin PDF parser for profile-configured option settlements.

Reads the product profile's CME option bulletin saved at
data/cme_bulletin/<date>.pdf.
Parses the configured CALL and PUT sections and outputs records in the bronze
format {"settlements": [...]} so that normalize_day.py picks them up unchanged.

PDF format (pdftotext -layout output):
  - product_header_call marks the call section
  - product_header_put marks the put section
  - Expiry headers: "AUG26", "SEP26" etc. (one per line)
  - Data rows: strike (cents) + 13 additional columns, right-parseable

Data row column order (left→right):
  STRIKE | GLOBEX_OPEN | OC_OPEN_RANGE | GLOBEX_HL | OC_HL | OC_CLOSE_RANGE
  | SETT_PRICE | [sign | UNCH] | PT_CHGE | DELTA | EXERCISES
  | OC_VOLUME | GLOBEX_VOLUME | PNT_VOLUME | OPEN_INTEREST | [OI_SIGN | UNCH] | OI_CHG

Expiry convention (per product profile + calendar module):
  "AUG26" → option expiry from the product calendar (WTI: futures LTD − 3
  business days → 2026-08-17). The product profile maps the option month to
  its underlying (a constant offset for WTI; a serial-month map for Gold).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

_MONTH_NAME_TO_NUM = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_EXPIRY_RE = re.compile(r'^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}$', re.IGNORECASE)
_PRODUCT_HEADER_RE = re.compile(r'^[A-Z]{2,5}\s+(CALL|PUT)\s+\S', re.IGNORECASE)
_DECIMAL_RE = re.compile(r'^\d*\.\d+$')
_INT_RE = re.compile(r'^\d+$')


def _expiry_code_to_option_info(code: str) -> tuple[date, str, str]:
    """
    'AUG26' → (option_expiry, underlying_contract, underlying_delivery_month)

    The product profile owns both option-month → underlying mapping and whether
    the expiry rule is keyed by option month or underlying month.
    """
    p = get_product()
    if p.bulletin is None:
        raise ValueError(f"Product {p.key!r} has no bulletin configuration")
    month_num = _MONTH_NAME_TO_NUM[code[:3].upper()]
    year = 2000 + int(code[3:])
    return p.option_contract_info(year, month_num)


def _pdftotext(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _parse_data_row(tokens: list[str]) -> Optional[dict]:
    """
    Parse a tokenized CME bulletin data row right-to-left.

    Returns a dict of raw field values or None if the row is not a valid
    data row (e.g., a header, footer, or malformed line).
    """
    if len(tokens) < 9:
        return None
    # Strike must be a 3-5 digit integer (in cents: 5650 = $56.50)
    if not re.match(r'^\d{3,5}$', tokens[0]):
        return None

    i = len(tokens) - 1

    # ── OI and OI change (rightmost) ──
    if tokens[i] == 'UNCH':
        oi_change = 0
        i -= 1
        if not _INT_RE.match(tokens[i]):
            return None
        oi = int(tokens[i])
        i -= 1
    elif _INT_RE.match(tokens[i]):
        if i >= 1 and tokens[i - 1] in ('+', '-'):
            oi_chg_mag = int(tokens[i])
            sign_char = tokens[i - 1]
            i -= 2
            if not _INT_RE.match(tokens[i]):
                return None
            oi = int(tokens[i])
            oi_change = oi_chg_mag if sign_char == '+' else -oi_chg_mag
            i -= 1
        else:
            oi = int(tokens[i])
            oi_change = 0
            i -= 1
    else:
        return None

    # ── PNT volume ──
    pnt_vol: Optional[int] = None if tokens[i] in ('----', '-') else (
        int(tokens[i]) if _INT_RE.match(tokens[i]) else None
    )
    i -= 1

    # ── GLOBEX volume ──
    globex_vol: Optional[int] = None if tokens[i] in ('----', '-') else (
        int(tokens[i]) if _INT_RE.match(tokens[i]) else None
    )
    i -= 1

    # ── OC volume ──
    oc_vol: Optional[int] = None if tokens[i] in ('----', '-') else (
        int(tokens[i]) if _INT_RE.match(tokens[i]) else None
    )
    i -= 1

    # ── Exercises (always one token, usually ----) ──
    i -= 1  # skip unconditionally

    if i < 2:
        return None

    # ── Delta ──
    if tokens[i] in ('----', '-'):
        delta: Optional[float] = None
        i -= 1
    elif _DECIMAL_RE.match(tokens[i]):
        delta = float(tokens[i])
        i -= 1
    else:
        return None

    if i < 2:
        return None

    # ── PT.CHGE: UNCH or [sign, value] ──
    if tokens[i] == 'UNCH':
        pt_change = 0.0
        i -= 1
    elif _DECIMAL_RE.match(tokens[i]):
        pt_change_val = float(tokens[i])
        i -= 1
        if i < 1 or tokens[i] not in ('+', '-'):
            return None
        pt_change = pt_change_val if tokens[i] == '+' else -pt_change_val
        i -= 1
    else:
        return None

    if i < 1:
        return None

    # ── Settlement price ──
    if not re.match(r'^\d+\.?\d*$', tokens[i]):
        return None
    settlement = float(tokens[i])

    return {
        'strike_cents': int(tokens[0]),
        'settlement': settlement,
        'pt_change': pt_change,
        'delta': delta,
        'oc_vol': oc_vol,
        'globex_vol': globex_vol,
        'pnt_vol': pnt_vol,
        'oi': oi,
        'oi_change': oi_change,
    }


def parse(pdf_path: Path, trade_date: date) -> list[dict]:
    """
    Parse configured option settlements from a CME daily bulletin PDF.
    Returns a list of bronze-layer record dicts.
    """
    text = _pdftotext(pdf_path)
    records: list[dict] = []
    product = get_product()
    if product.bulletin is None:
        raise ValueError(f"Product {product.key!r} has no bulletin configuration")

    in_lo_call = False
    in_lo_put = False
    current_expiry_code: Optional[str] = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # ── Detect product section headers ──
        if _PRODUCT_HEADER_RE.match(stripped):
            # Section headers from the product profile (E1): e.g. "LO CALL" /
            # "LO PUT" for WTI, "LN CALL"/"LN PUT" for Henry Hub.
            p = product
            hdr_call = re.escape(p.bulletin.product_header_call).replace(r"\ ", r"\s+")
            hdr_put = re.escape(p.bulletin.product_header_put).replace(r"\ ", r"\s+")
            lo_call = bool(re.match(rf"^{hdr_call}\s+\S", stripped, re.IGNORECASE))
            lo_put = bool(re.match(rf"^{hdr_put}\s+\S", stripped, re.IGNORECASE))
            if lo_call:
                in_lo_call, in_lo_put = True, False
            elif lo_put:
                in_lo_call, in_lo_put = False, True
            else:
                in_lo_call, in_lo_put = False, False
            current_expiry_code = None
            continue

        if not (in_lo_call or in_lo_put):
            continue

        tokens = stripped.split()
        if not tokens:
            continue

        # ── Expiry month header (e.g., "AUG26") ──
        if len(tokens) == 1 and _EXPIRY_RE.match(tokens[0]):
            current_expiry_code = tokens[0].upper()
            continue

        if current_expiry_code is None:
            continue

        # ── TOTAL line marks end of expiry block ──
        if tokens[0].upper() == 'TOTAL':
            continue

        # ── Try to parse as a data row ──
        row = _parse_data_row(tokens)
        if row is None:
            continue

        # Map expiry code to dates
        try:
            option_expiry, underlying_contract, underlying_delivery_month = (
                _expiry_code_to_option_info(current_expiry_code)
            )
        except (KeyError, ValueError) as exc:
            logger.warning("Could not parse expiry code %r: %s — skipping", current_expiry_code, exc)
            continue

        if option_expiry <= trade_date:
            continue

        call_put = "C" if in_lo_call else "P"
        strike = row["strike_cents"] / product.bulletin.strike_scale

        # Negate put delta (bulletin shows absolute value; convention is negative)
        raw_delta = row['delta']
        if raw_delta is not None and call_put == 'P':
            raw_delta = -raw_delta

        vol_parts = [v for v in (row['oc_vol'], row['globex_vol'], row['pnt_vol']) if v is not None]
        total_volume = sum(vol_parts) if vol_parts else None

        records.append({
            "trade_date": trade_date.isoformat(),
            "option_expiry": option_expiry.isoformat(),
            "option_symbol": f"{product.options_prefix}{current_expiry_code}{call_put}{row['strike_cents']:05d}",
            "underlying_contract": underlying_contract,
            "underlying_delivery_month": underlying_delivery_month,
            "strike": strike,
            "call_put": call_put,
            "settlement": row['settlement'],
            "bid": None,
            "ask": None,
            "volume": total_volume,
            "open_interest": row['oi'],
            "implied_volatility": None,
            "delta": raw_delta,
            "gamma": None,
            "theta": None,
            "vega": None,
            "exercise_style": product.exercise_style,
            "settlement_style": product.settlement_style,
            "contract_multiplier": int(product.contract_multiplier),
            "source_id": f"cme_bulletin_{product.options_prefix.lower()}_option",
            "price_note": "CME_daily_bulletin_settlement",
        })

    return records


class CMEBulletinPDFCollector:
    """
    Collects profile-configured option settlements from a downloaded CME daily
    bulletin PDF.  The PDF must be saved at data/cme_bulletin/<YYYY-MM-DD>.pdf
    before running collect().

    source_id = "cme_bulletin_lo_option" (contains "option" so normalize_day.py
    picks it up automatically).
    """

    def __init__(self, data_dir: Path, raw_store: RawStore, manifest_db: ManifestDB) -> None:
        self.bulletin_dir = data_dir / "cme_bulletin"
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        product = get_product()
        self.source_id = f"cme_bulletin_{product.options_prefix.lower()}_option"

    def pdf_path(self, trade_date: date) -> Path:
        return self.bulletin_dir / f"{trade_date.isoformat()}.pdf"

    def collect(self, trade_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = trade_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        pdf = self.pdf_path(trade_date)
        if not pdf.exists():
            msg = f"CME bulletin PDF not found: {pdf} — download it manually first"
            logger.warning(msg)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=msg)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        try:
            records = parse(pdf, trade_date)
        except subprocess.CalledProcessError as exc:
            msg = f"pdftotext failed (is poppler installed?): {exc}"
            logger.error(msg)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=msg)
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0}
        except Exception as exc:
            msg = f"Parse error: {exc}"
            logger.error("Failed to parse CME bulletin PDF %s: %s", pdf, exc)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=msg)
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0}

        if not records:
            product = get_product()
            msg = f"No {product.options_prefix} option records found in PDF"
            logger.warning("%s — check the configured bulletin source for %s", msg, pdf.name)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=msg)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        content = json.dumps({"settlements": records}, indent=2).encode()
        sha256 = hashlib.sha256(content).hexdigest()

        if self.manifest_db.sha256_exists(sha256):
            logger.info("Skipping CME bulletin %s — already in manifest", trade_date)
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        prefix = get_product().options_prefix.lower()
        filename = f"cme_{prefix}_options_{trade_date.strftime('%Y%m%d')}.json"
        raw_path, sha256_written, byte_size = self.raw_store.persist(
            content=content,
            source_id=self.source_id,
            filename=filename,
            trade_date=as_of_str,
            source_url=f"file://{pdf}",
            content_type="application/json",
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": self.source_id,
            "raw_path": str(raw_path),
            "sha256": sha256_written,
            "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": as_of_str,
            "source_url": f"file://{pdf}",
            "http_status": None,
            "content_type": "application/json",
            "collection_run_id": run_id,
        })

        calls = sum(1 for r in records if r["call_put"] == "C")
        puts = sum(1 for r in records if r["call_put"] == "P")
        logger.info(
            "CME bulletin %s: %d records (%d calls, %d puts) → %s",
            trade_date, len(records), calls, puts, raw_path.name,
        )
        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0,
                                      notes=f"{len(records)} records ({calls}C/{puts}P)")
        return {"run_id": run_id, "status": "success", "success": 1,
                "warning": 0, "failure": 0, "skipped": 0}

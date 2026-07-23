from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from ..reference.product import get_product

QualityStatus = Literal["PASS", "WARN", "FAIL", "QUARANTINE"]

@dataclass
class QualityResult:
    status: QualityStatus
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    checks_warned: list[str] = field(default_factory=list)
    record_count: int = 0
    valid_count: int = 0
    notes: str = ""

    def _resolve_status(self) -> None:
        if self.checks_failed:
            self.status = "FAIL"
        elif self.checks_warned:
            self.status = "WARN"
        else:
            self.status = "PASS"


def _parse_contract_delivery(contract_code: str) -> tuple[int, int] | None:
    """Parse a contract code using the active product profile."""
    return get_product().parse_contract_code(contract_code)


def check_futures_settlements(records: list[dict], source_id: str) -> QualityResult:
    """
    Validate a list of futures settlement dicts (as read from CSV).
    Returns a QualityResult with PASS / WARN / FAIL.
    """
    result = QualityResult(status="PASS", record_count=len(records))

    if not records:
        result.checks_warned.append("no_records: empty dataset")
        result.status = "WARN"
        result.notes = "No records to validate"
        return result

    # --- 1. Duplicate natural keys ---
    natural_keys: list[tuple] = []
    for r in records:
        key = (
            r.get("trade_date"), r.get("exchange"), r.get("product"),
            r.get("contract_code"), source_id,
        )
        natural_keys.append(key)

    if len(natural_keys) != len(set(natural_keys)):
        dupes = [k for k in natural_keys if natural_keys.count(k) > 1]
        result.checks_failed.append(f"duplicate_natural_keys: {set(map(str, dupes))}")
    else:
        result.checks_passed.append("no_duplicate_natural_keys")

    # --- 2. Contract code / delivery_month consistency ---
    mismatch_count = 0
    for r in records:
        cc = r.get("contract_code", "")
        dm = r.get("delivery_month", "")
        parsed = _parse_contract_delivery(cc)
        if parsed is None:
            continue
        year, month = parsed
        expected_dm = f"{year:04d}-{month:02d}"
        if dm != expected_dm:
            mismatch_count += 1

    if mismatch_count > 0:
        result.checks_failed.append(f"contract_delivery_month_mismatch: {mismatch_count} row(s)")
    else:
        result.checks_passed.append("contract_delivery_month_consistent")

    # --- 3. Settlement > 0 ---
    invalid_settlement = [
        r.get("contract_code") for r in records
        if float(r.get("settlement", 0)) <= 0
    ]
    if invalid_settlement:
        result.checks_failed.append(f"non_positive_settlement: {invalid_settlement}")
    else:
        result.checks_passed.append("settlement_positive")

    # --- 4. Non-monotonic delivery months (WARN) ---
    delivery_months = []
    for r in records:
        dm = r.get("delivery_month", "")
        if re.match(r"^\d{4}-\d{2}$", dm):
            delivery_months.append(dm)
    sorted_dm = sorted(delivery_months)
    if delivery_months and delivery_months != sorted_dm:
        result.checks_warned.append("non_monotonic_delivery_months")
    else:
        result.checks_passed.append("delivery_months_monotonic")

    # --- 5. Volume and OI non-negative (WARN) ---
    bad_vol = [r.get("contract_code") for r in records
               if r.get("volume") is not None and str(r.get("volume", "")).strip() != ""
               and float(r.get("volume", 0)) < 0]
    if bad_vol:
        result.checks_warned.append(f"negative_volume: {bad_vol}")
    else:
        result.checks_passed.append("volume_non_negative")

    result.valid_count = len(records) - mismatch_count
    result._resolve_status()
    return result


def _infer_correct_underlying(option_expiry_str: str) -> tuple[str, str] | None:
    """
    Given an option expiry date, infer the expected underlying futures month
    by resolving nearby bulletin option months through the product profile.
    This supports both constant offsets (WTI) and serial-month maps (Gold).
    Returns (expected_delivery_month_str, expected_contract_letter).
    """
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", option_expiry_str)
    if not m:
        return None
    expiry = date.fromisoformat(option_expiry_str)
    product = get_product()
    if product.bulletin is None:
        return None
    # Expiration normally falls in the option month or the immediately
    # preceding month. Include one extra month for holiday-shifted conventions.
    nearby_candidates: list[tuple[int, str, str | None]] = []
    for offset in range(3):
        total = expiry.month + offset - 1
        option_month = total % 12 + 1
        option_year = expiry.year + total // 12
        try:
            candidate_expiry, _, delivery_month = product.option_contract_info(
                option_year, option_month,
            )
        except (KeyError, ValueError):
            continue
        if candidate_expiry == expiry:
            dm_month = int(delivery_month[5:7])
            return delivery_month, product.month_letters.get(dm_month)
        dm_month = int(delivery_month[5:7])
        nearby_candidates.append((
            abs((candidate_expiry - expiry).days),
            delivery_month,
            product.month_letters.get(dm_month),
        ))
    # Legacy fixtures can differ from the holiday-aware calendar by one
    # business day. Select a very-near calendar match before falling back to a
    # month-label heuristic; this remains correct when an option expires in the
    # month preceding its same-named WTI underlying.
    if nearby_candidates:
        days, delivery_month, letter = min(nearby_candidates)
        if days <= 7:
            return delivery_month, letter
    # Compatibility for legacy fixtures whose expiry date predates the
    # corrected calendar but whose expiry month still encodes the option label.
    dm_year, dm_month = product.bulletin.underlying_month(expiry.year, expiry.month)
    return f"{dm_year:04d}-{dm_month:02d}", product.month_letters.get(dm_month)


def check_option_settlements(
    records: list[dict],
    futures_records: list[dict],
    source_id: str,
) -> QualityResult:
    """
    Validate a list of option settlement dicts.
    futures_records is used for cross-referencing underlying contracts.
    """
    result = QualityResult(status="PASS", record_count=len(records))

    if not records:
        result.checks_warned.append("no_records: empty option dataset")
        result.status = "WARN"
        return result

    trade_date_str = records[0].get("trade_date", "")
    try:
        trade_date = date.fromisoformat(str(trade_date_str))
    except ValueError:
        result.checks_failed.append(f"invalid_trade_date: {trade_date_str}")
        result.status = "FAIL"
        return result

    # --- 1. Duplicate natural keys ---
    natural_keys = [
        (
            r.get("trade_date"), r.get("option_expiry"), r.get("underlying_contract"),
            r.get("strike"), r.get("call_put"), source_id,
        )
        for r in records
    ]
    if len(natural_keys) != len(set(natural_keys)):
        result.checks_failed.append("duplicate_natural_keys")
    else:
        result.checks_passed.append("no_duplicate_natural_keys")

    # --- 2. Option expiry > trade_date ---
    expired = [
        r.get("option_expiry") for r in records
        if date.fromisoformat(str(r.get("option_expiry", "1900-01-01"))) <= trade_date
    ]
    if expired:
        result.checks_failed.append(f"option_expiry_not_after_trade_date: {expired}")
    else:
        result.checks_passed.append("option_expiry_after_trade_date")

    # --- 3. Underlying contract consistency ---
    wrong_underlying = []
    for r in records:
        expiry_str = str(r.get("option_expiry", ""))
        inferred = _infer_correct_underlying(expiry_str)
        if inferred is None:
            continue
        expected_dm, expected_letter = inferred
        actual_underlying = r.get("underlying_contract", "")
        actual_dm = r.get("underlying_delivery_month", "")

        # Check delivery month
        if actual_dm != expected_dm:
            wrong_underlying.append(
                f"{actual_underlying} (expiry={expiry_str}, expected_dm={expected_dm}, got={actual_dm})"
            )
            continue

        # Check contract letter
        parsed = _parse_contract_delivery(actual_underlying)
        if parsed:
            _, actual_month = parsed
            if actual_month != get_product().month_codes.get(expected_letter, -1):
                wrong_underlying.append(
                    f"{actual_underlying} (expiry={expiry_str}, expected_letter={expected_letter})"
                )

    if wrong_underlying:
        result.checks_failed.append(f"wrong_underlying_contract: {wrong_underlying[:3]}")
    else:
        result.checks_passed.append("underlying_contract_consistent")

    # --- 4. Settlement >= 0 ---
    negative_settlements = [
        str(r.get("strike")) for r in records
        if float(r.get("settlement", 0)) < 0
    ]
    if negative_settlements:
        result.checks_failed.append(f"negative_settlement: strikes={negative_settlements}")
    else:
        result.checks_passed.append("settlement_non_negative")

    # --- 5. Strike count per expiry ---
    from collections import defaultdict
    strikes_per_expiry: dict[str, set] = defaultdict(set)
    for r in records:
        key = (r.get("option_expiry"), r.get("underlying_contract"), r.get("call_put"))
        strikes_per_expiry[str(key)].add(r.get("strike"))

    sparse_expiries = []
    fail_sparse = []
    product = get_product()
    for key, strikes in strikes_per_expiry.items():
        n = len(strikes)
        if n < product.fail_strikes_below:
            fail_sparse.append(f"{key}: {n} strikes")
        elif n < product.pass_strikes_at:
            sparse_expiries.append(f"{key}: {n} strikes")

    if fail_sparse:
        result.checks_failed.append(f"critically_sparse_strikes: {fail_sparse}")
    elif sparse_expiries:
        result.checks_warned.append(
            f"sparse_strikes_below_{product.pass_strikes_at}: {sparse_expiries}")
    else:
        result.checks_passed.append("sufficient_strikes_per_expiry")

    result.valid_count = len(records)
    result._resolve_status()
    return result

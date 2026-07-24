"""Validate ICE Report Center CSV exports and build canonical Brent handoffs."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from ccvm.reference.product import Product

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11,
    "DEC": 12,
}


def _key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name, "").strip()
        if value:
            return value
    return ""


def _parse_date(value: str) -> date:
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported ICE trade date {value!r}")


def _parse_strip(value: str) -> tuple[int, int]:
    compact = re.sub(r"[\s/_-]+", "", value.strip().upper())
    match = re.fullmatch(r"([A-Z]{3,4})(\d{2}|\d{4})", compact)
    if match and match.group(1) in _MONTHS:
        year = int(match.group(2))
        return (2000 + year if year < 100 else year), _MONTHS[match.group(1)]
    match = re.fullmatch(r"(\d{4})(\d{2})", compact)
    if match and 1 <= int(match.group(2)) <= 12:
        return int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"(\d{2})(\d{4})", compact)
    if match and 1 <= int(match.group(1)) <= 12:
        return int(match.group(2)), int(match.group(1))
    try:
        parsed = _parse_date(value)
        return parsed.year, parsed.month
    except ValueError as exc:
        raise ValueError(f"unsupported ICE strip {value!r}") from exc


def _number(value: str, label: str, *, positive: bool = False) -> float:
    cleaned = value.strip().replace(",", "")
    if cleaned in {"", "-", "N/A", "NA"}:
        raise ValueError(f"missing {label}")
    try:
        result = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"invalid {label} {value!r}") from exc
    if positive and result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _optional_number(row: dict[str, str], *names: str) -> float | None:
    value = _value(row, *names)
    if not value or value.upper() in {"-", "N/A", "NA"}:
        return None
    return _number(value, names[0].lower())


def _is_brent(row: dict[str, str], contract: str) -> bool:
    identity = " ".join(
        _value(row, name)
        for name in ("PRODUCT", "COMMODITY", "LONG_NAME", "HUB", "CONTRACT")
    ).upper()
    return "BRENT" in identity or bool(
        re.search(rf"(?:^|\W){re.escape(contract.upper())}(?:$|\W)", identity)
    )


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path.name} has no CSV header")
        rows = [
            {_key(str(key)): str(value or "").strip() for key, value in row.items()}
            for row in reader
            if any(str(value or "").strip() for value in row.values())
        ]
    if not rows:
        raise ValueError(f"{path.name} has no data rows")
    return rows


def _identity_and_date_rows(
    path: Path, expected_date: date, contract: str,
) -> tuple[list[dict[str, str]], int]:
    rows = _read_rows(path)
    selected: list[dict[str, str]] = []
    excluded = 0
    observed_dates: set[date] = set()
    for index, row in enumerate(rows, start=2):
        raw_date = _value(row, "TRADE_DATE", "TRADEDATE", "DATE")
        if not raw_date:
            raise ValueError(f"{path.name} row {index} has no trade date")
        observed = _parse_date(raw_date)
        observed_dates.add(observed)
        if observed != expected_date:
            continue
        if _is_brent(row, contract):
            selected.append(row)
        else:
            excluded += 1
    if expected_date not in observed_dates:
        values = ", ".join(sorted(item.isoformat() for item in observed_dates))
        raise ValueError(
            f"{path.name} does not contain requested trade date "
            f"{expected_date.isoformat()}; found {values}"
        )
    if not selected:
        raise ValueError(
            f"{path.name} has no rows identifiable as ICE Brent contract {contract}"
        )
    return selected, excluded


def _deduplicate(rows: list[dict], key_fields: tuple[str, ...], label: str) -> list[dict]:
    unique: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        prior = unique.get(key)
        if prior is not None and prior != row:
            raise ValueError(f"conflicting duplicate {label} row for {key}")
        unique[key] = row
    return [unique[key] for key in sorted(unique)]


def _futures(
    path: Path, expected_date: date, product: Product,
) -> tuple[list[dict], int]:
    rows, excluded = _identity_and_date_rows(
        path, expected_date, product.product_code,
    )
    result = []
    for row in rows:
        settlement_raw = _value(row, "SETTLEMENT_PRICE", "SETTLE", "SETTLEMENT")
        if not settlement_raw or settlement_raw.upper() in {"-", "N/A", "NA"}:
            continue
        year, month = _parse_strip(
            _value(row, "STRIP", "CONTRACT_MONTH", "DELIVERY_MONTH")
        )
        item = {
            "trade_date": expected_date.isoformat(),
            "contract_code": product.contract_code(year, month),
            "delivery_month": f"{year:04d}-{month:02d}",
            "settlement": _number(settlement_raw, "settlement"),
        }
        for target, aliases in (
            ("volume", ("TOTAL_VOLUME", "VOLUME")),
            ("open_interest", ("OPEN_INTEREST",)),
        ):
            value = _optional_number(row, *aliases)
            if value is not None:
                item[target] = value
        result.append(item)
    if not result:
        raise ValueError(f"{path.name} has no settled Brent futures rows")
    return _deduplicate(result, ("contract_code",), "futures"), excluded


def _options(
    path: Path, expected_date: date, product: Product,
) -> tuple[list[dict], int]:
    rows, excluded = _identity_and_date_rows(
        path, expected_date, product.product_code,
    )
    result = []
    for row in rows:
        settlement_raw = _value(row, "SETTLEMENT_PRICE", "SETTLE", "SETTLEMENT")
        if not settlement_raw or settlement_raw.upper() in {"-", "N/A", "NA"}:
            continue
        year, month = _parse_strip(
            _value(row, "STRIP", "CONTRACT_MONTH", "DELIVERY_MONTH")
        )
        put_call = _value(row, "PUT_CALL", "PUTCALL", "OPTION_TYPE").upper()
        put_call = {"CALL": "C", "PUT": "P"}.get(put_call, put_call)
        if put_call not in {"C", "P"}:
            raise ValueError(f"invalid ICE put/call value {put_call!r}")
        item = {
            "trade_date": expected_date.isoformat(),
            "option_expiry": product.calendar.option_expiry_date(
                year, month,
            ).isoformat(),
            "underlying_contract": product.contract_code(year, month),
            "underlying_delivery_month": f"{year:04d}-{month:02d}",
            "strike": _number(_value(row, "STRIKE", "STRIKE_PRICE"), "strike", positive=True),
            "call_put": put_call,
            "settlement": _number(settlement_raw, "settlement"),
        }
        for target, aliases in (
            ("volume", ("TOTAL_VOLUME", "VOLUME")),
            ("open_interest", ("OPEN_INTEREST",)),
            ("implied_vol", ("OPTION_VOLATILITY", "IMPLIED_VOLATILITY", "IV")),
            ("delta", ("DELTA_FACTOR", "DELTA")),
        ):
            value = _optional_number(row, *aliases)
            if value is not None:
                item[target] = value
        result.append(item)
    if not result:
        raise ValueError(f"{path.name} has no settled Brent option rows")
    return _deduplicate(
        result, ("underlying_contract", "strike", "call_put"), "options",
    ), excluded


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_exact(source: Path, destination: Path) -> None:
    if destination.exists():
        if _sha256(source) != _sha256(destination):
            raise ValueError(
                f"{destination} already contains different source bytes; "
                "preserve and review the existing licensed export"
            )
        return
    shutil.copy2(source, destination)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False,
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


@dataclass(frozen=True)
class ImportResult:
    futures_path: Path
    options_path: Path
    manifest_path: Path
    futures_rows: int
    options_rows: int


def import_brent_reports(
    *,
    futures_csv: Path,
    options_csv: Path,
    trade_date: date,
    data_dir: Path,
    product: Product,
) -> ImportResult:
    """Import authorized ICE reports without making network or model calls."""
    if product.key != "brent" or product.market_data is None:
        raise ValueError("ICE Brent report import requires CCVM_PRODUCT=brent")
    spec = product.market_data
    if not spec.futures_source_url or not spec.options_source_url:
        raise ValueError("active profile does not declare ICE report sources")
    if spec.source_contract != product.product_code:
        raise ValueError(
            "ICE source contract does not match the active product code"
        )
    futures_csv = Path(futures_csv).resolve()
    options_csv = Path(options_csv).resolve()
    if not futures_csv.is_file() or not options_csv.is_file():
        raise ValueError("both ICE futures and options CSV files must exist")

    futures_rows, futures_excluded = _futures(futures_csv, trade_date, product)
    options_rows, options_excluded = _options(options_csv, trade_date, product)
    canonical_dir = (
        Path(data_dir) / spec.input_subdir / f"trade_date={trade_date.isoformat()}"
    )
    archive_dir = (
        Path(data_dir) / "ice_report_center" / f"trade_date={trade_date.isoformat()}"
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_futures = archive_dir / "report-10-futures.csv"
    archived_options = archive_dir / "report-166-options.csv"
    if futures_csv != archived_futures.resolve():
        _archive_exact(futures_csv, archived_futures)
    if options_csv != archived_options.resolve():
        _archive_exact(options_csv, archived_options)

    base = {
        "trade_date": trade_date.isoformat(),
        "exchange": product.exchange,
        "product": product.product_code,
    }
    futures_path = canonical_dir / spec.futures_filename
    options_path = canonical_dir / spec.options_filename
    _atomic_json(futures_path, {**base, "settlements": futures_rows})
    _atomic_json(options_path, {**base, "settlements": options_rows})
    manifest_path = archive_dir / "source_manifest.json"
    _atomic_json(manifest_path, {
        "trade_date": trade_date.isoformat(),
        "exchange": product.exchange,
        "product": product.product_code,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "futures": {
                "report_url": spec.futures_source_url,
                "downloaded_filename": futures_csv.name,
                "archived_path": str(archived_futures),
                "sha256": _sha256(archived_futures),
                "rows": len(futures_rows),
                "excluded_non_brent_rows": futures_excluded,
            },
            "options": {
                "report_url": spec.options_source_url,
                "downloaded_filename": options_csv.name,
                "archived_path": str(archived_options),
                "sha256": _sha256(archived_options),
                "rows": len(options_rows),
                "excluded_non_brent_rows": options_excluded,
            },
        },
    })
    return ImportResult(
        futures_path=futures_path,
        options_path=options_path,
        manifest_path=manifest_path,
        futures_rows=len(futures_rows),
        options_rows=len(options_rows),
    )

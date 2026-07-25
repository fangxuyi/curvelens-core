"""Validate ICE Report Center CSV/PDF exports and build Brent handoffs."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
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
_FUTURES_PDF_TITLE = "Futures Daily Market Report for ICE Brent Futures"
_OPTIONS_PDF_TITLE = "Options Daily Market Report for ICE Brent Futures"
_PDF_DATE = re.compile(r"^\s*(\d{1,2}-[A-Za-z]{3}-\d{4})\s*$", re.MULTILINE)
_PDF_ROW = re.compile(r"^\s*B\s+[A-Z][a-z]{2}\d{2}\s+")


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


def _report_date(text: str, title: str, identity: str) -> date:
    if title not in text:
        raise ValueError(f"PDF is not {title!r}")
    if identity not in text:
        raise ValueError(f"PDF does not identify {identity}")
    match = _PDF_DATE.search(text)
    if not match:
        raise ValueError("PDF has no recognizable internal report date")
    try:
        return datetime.strptime(match.group(1), "%d-%b-%Y").date()
    except ValueError as exc:
        raise ValueError(
            f"PDF has invalid internal report date {match.group(1)!r}"
        ) from exc


def _integer(value: str, label: str) -> int:
    number = _number(value, label)
    if not number.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(number)


def parse_brent_futures_pdf_text(
    text: str, expected_date: date, product: Product,
) -> list[dict]:
    """Parse layout-preserved text from official ICE futures Report 10."""
    observed = _report_date(text, _FUTURES_PDF_TITLE, "B-Brent Crude Future")
    if observed != expected_date:
        raise ValueError(
            f"futures PDF report date {observed.isoformat()} does not match "
            f"{expected_date.isoformat()}"
        )
    result = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not _PDF_ROW.match(line):
            continue
        parts = line.split()
        if len(parts) < 11 or parts[0] != product.product_code:
            raise ValueError(
                f"futures PDF row {line_number} has an unexpected layout"
            )
        year, month = _parse_strip(parts[1])
        tail = parts[-9:]
        settlement = _number(tail[0], "settlement")
        if settlement < 0:
            raise ValueError(
                f"futures PDF row {line_number} has negative settlement"
            )
        result.append({
            "trade_date": expected_date.isoformat(),
            "contract_code": product.contract_code(year, month),
            "delivery_month": f"{year:04d}-{month:02d}",
            "settlement": settlement,
            "settlement_change": _number(tail[1], "settlement change"),
            "volume": _integer(tail[2], "volume"),
            "open_interest": _integer(tail[3], "open interest"),
        })
    if not result:
        raise ValueError("futures PDF has no Brent settlement rows")
    return _deduplicate(result, ("contract_code",), "futures")


def parse_brent_options_pdf_text(
    text: str, expected_date: date, product: Product,
) -> list[dict]:
    """Parse layout-preserved text from official ICE options Report 166."""
    observed = _report_date(
        text, _OPTIONS_PDF_TITLE, "B-Option on Brent Crude Future",
    )
    if observed != expected_date:
        raise ValueError(
            f"options PDF report date {observed.isoformat()} does not match "
            f"{expected_date.isoformat()}"
        )
    result = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not _PDF_ROW.match(line):
            continue
        parts = line.split()
        if len(parts) < 14 or parts[0] != product.product_code:
            raise ValueError(
                f"options PDF row {line_number} has an unexpected layout"
            )
        put_call = parts[3].upper()
        if put_call not in {"C", "P"}:
            raise ValueError(
                f"options PDF row {line_number} has invalid put/call"
            )
        year, month = _parse_strip(parts[1])
        tail = parts[-9:]
        settlement = _number(tail[0], "settlement")
        if settlement < 0:
            raise ValueError(
                f"options PDF row {line_number} has negative settlement"
            )
        result.append({
            "trade_date": expected_date.isoformat(),
            "option_expiry": product.calendar.option_expiry_date(
                year, month,
            ).isoformat(),
            "underlying_contract": product.contract_code(year, month),
            "underlying_delivery_month": f"{year:04d}-{month:02d}",
            "strike": _number(parts[2], "strike", positive=True),
            "call_put": put_call,
            "settlement": settlement,
            "settlement_change": _number(tail[1], "settlement change"),
            "volume": _integer(tail[2], "volume"),
            "open_interest": _integer(tail[3], "open interest"),
            "delta": _number(parts[4], "delta"),
        })
    if not result:
        raise ValueError("options PDF has no Brent settlement rows")
    return _deduplicate(
        result, ("underlying_contract", "strike", "call_put"), "options",
    )


def _extract_pdf_text(path: Path) -> str:
    with path.open("rb") as handle:
        signature = handle.read(5)
    if signature != b"%PDF-":
        raise ValueError(f"{path.name} is not a PDF")
    executable = shutil.which("pdftotext")
    if executable is None:
        raise ValueError("pdftotext is required to import ICE PDF reports")
    process = subprocess.run(
        [executable, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        detail = " ".join(process.stderr.split())
        raise ValueError(f"pdftotext failed for {path.name}: {detail}")
    if not process.stdout.strip():
        raise ValueError(f"{path.name} contains no extractable text")
    return process.stdout


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


def _validate_product(product: Product) -> None:
    if product.key != "brent" or product.market_data is None:
        raise ValueError("ICE Brent report import requires CCVM_PRODUCT=brent")
    spec = product.market_data
    if not spec.futures_source_url or not spec.options_source_url:
        raise ValueError("active profile does not declare ICE report sources")
    if spec.source_contract != product.product_code:
        raise ValueError(
            "ICE source contract does not match the active product code"
        )


def _write_import(
    *,
    futures_source: Path,
    options_source: Path,
    futures_rows: list[dict],
    options_rows: list[dict],
    source_format: str,
    trade_date: date,
    data_dir: Path,
    product: Product,
    futures_excluded: int = 0,
    options_excluded: int = 0,
) -> ImportResult:
    """Persist validated rows and exact authorized source files."""
    _validate_product(product)
    spec = product.market_data
    assert spec is not None
    futures_source = Path(futures_source).resolve()
    options_source = Path(options_source).resolve()
    if source_format not in {"csv", "pdf"}:
        raise ValueError(f"unsupported ICE source format {source_format!r}")
    canonical_dir = (
        Path(data_dir) / spec.input_subdir / f"trade_date={trade_date.isoformat()}"
    )
    archive_dir = (
        Path(data_dir) / "ice_report_center" / f"trade_date={trade_date.isoformat()}"
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_futures = archive_dir / f"report-10-futures.{source_format}"
    archived_options = archive_dir / f"report-166-options.{source_format}"
    if futures_source != archived_futures.resolve():
        _archive_exact(futures_source, archived_futures)
    if options_source != archived_options.resolve():
        _archive_exact(options_source, archived_options)

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
                "format": source_format,
                "downloaded_filename": futures_source.name,
                "archived_path": str(archived_futures),
                "sha256": _sha256(archived_futures),
                "rows": len(futures_rows),
                "excluded_non_brent_rows": futures_excluded,
            },
            "options": {
                "report_url": spec.options_source_url,
                "format": source_format,
                "downloaded_filename": options_source.name,
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


def import_brent_reports(
    *,
    futures_csv: Path,
    options_csv: Path,
    trade_date: date,
    data_dir: Path,
    product: Product,
) -> ImportResult:
    """Import authorized ICE CSV reports without network or model calls."""
    _validate_product(product)
    futures_csv = Path(futures_csv).resolve()
    options_csv = Path(options_csv).resolve()
    if not futures_csv.is_file() or not options_csv.is_file():
        raise ValueError("both ICE futures and options CSV files must exist")
    futures_rows, futures_excluded = _futures(
        futures_csv, trade_date, product,
    )
    options_rows, options_excluded = _options(
        options_csv, trade_date, product,
    )
    return _write_import(
        futures_source=futures_csv,
        options_source=options_csv,
        futures_rows=futures_rows,
        options_rows=options_rows,
        source_format="csv",
        trade_date=trade_date,
        data_dir=data_dir,
        product=product,
        futures_excluded=futures_excluded,
        options_excluded=options_excluded,
    )


def import_brent_pdf_reports(
    *,
    futures_pdf: Path,
    options_pdf: Path,
    trade_date: date,
    data_dir: Path,
    product: Product,
) -> ImportResult:
    """Import official ICE text PDFs without network or model calls."""
    _validate_product(product)
    futures_pdf = Path(futures_pdf).resolve()
    options_pdf = Path(options_pdf).resolve()
    if not futures_pdf.is_file() or not options_pdf.is_file():
        raise ValueError("both ICE futures and options PDF files must exist")
    futures_rows = parse_brent_futures_pdf_text(
        _extract_pdf_text(futures_pdf), trade_date, product,
    )
    options_rows = parse_brent_options_pdf_text(
        _extract_pdf_text(options_pdf), trade_date, product,
    )
    return _write_import(
        futures_source=futures_pdf,
        options_source=options_pdf,
        futures_rows=futures_rows,
        options_rows=options_rows,
        source_format="pdf",
        trade_date=trade_date,
        data_dir=data_dir,
        product=product,
    )


def _pdf_date(path: Path, title: str, identity: str) -> date:
    return _report_date(_extract_pdf_text(path), title, identity)


def import_brent_pdf_directory(
    *,
    batch_root: Path,
    data_dir: Path,
    product: Product,
) -> list[ImportResult]:
    """Pair and import every official futures/options PDF by internal date."""
    _validate_product(product)
    batch_root = Path(batch_root).resolve()
    groups: dict[str, dict[date, Path]] = {}
    for kind, title, identity in (
        ("futures", _FUTURES_PDF_TITLE, "B-Brent Crude Future"),
        ("options", _OPTIONS_PDF_TITLE, "B-Option on Brent Crude Future"),
    ):
        directory = batch_root / kind
        if not directory.is_dir():
            raise ValueError(f"batch directory is missing {directory}")
        dated: dict[date, Path] = {}
        for path in sorted(directory.glob("*.pdf")):
            internal_date = _pdf_date(path, title, identity)
            if internal_date in dated:
                raise ValueError(
                    f"duplicate {kind} PDFs for {internal_date.isoformat()}: "
                    f"{dated[internal_date].name}, {path.name}"
                )
            dated[internal_date] = path
        if not dated:
            raise ValueError(f"batch directory has no {kind} PDFs")
        groups[kind] = dated

    futures_dates = set(groups["futures"])
    options_dates = set(groups["options"])
    if futures_dates != options_dates:
        missing_futures = sorted(options_dates - futures_dates)
        missing_options = sorted(futures_dates - options_dates)
        raise ValueError(
            "unpaired ICE PDFs; "
            f"missing futures={[item.isoformat() for item in missing_futures]}, "
            f"missing options={[item.isoformat() for item in missing_options]}"
        )
    return [
        import_brent_pdf_reports(
            futures_pdf=groups["futures"][trade_date],
            options_pdf=groups["options"][trade_date],
            trade_date=trade_date,
            data_dir=data_dir,
            product=product,
        )
        for trade_date in sorted(futures_dates)
    ]

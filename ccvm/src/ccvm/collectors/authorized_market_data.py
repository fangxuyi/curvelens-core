"""Ingest product-scoped, authorized exchange settlement exports.

This collector does not call a vendor API. An operating agent obtains the
licensed files through the deployment's approved channel and places two
canonical JSON documents at the profile-configured handoff paths. The
collector validates identity and trade date, then stores immutable raw copies
for the existing bronze/silver pipeline.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from ..reference.product import get_product
from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore


class AuthorizedMarketDataCollector:
    def __init__(
        self, data_dir: Path, raw_store: RawStore, manifest_db: ManifestDB,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.product = get_product()
        self.spec = self.product.market_data
        if self.spec is None or self.spec.provider != "authorized_files":
            raise ValueError(
                f"Product {self.product.key!r} does not use authorized market files"
            )

    def required_paths(self, as_of_date: date) -> tuple[Path, Path]:
        return self.spec.required_paths(self.data_dir, as_of_date.isoformat())

    def _validate(self, path: Path, trade_date: str, kind: str) -> bytes:
        data = json.loads(path.read_text())
        if data.get("trade_date") != trade_date:
            raise ValueError(
                f"{path.name} trade_date {data.get('trade_date')!r} "
                f"does not match {trade_date}"
            )
        if str(data.get("exchange", "")).upper() != self.product.exchange.upper():
            raise ValueError(
                f"{path.name} exchange must be {self.product.exchange!r}"
            )
        if str(data.get("product", "")).upper() != self.product.product_code.upper():
            raise ValueError(
                f"{path.name} product must be {self.product.product_code!r}"
            )
        settlements = data.get("settlements")
        if not isinstance(settlements, list) or not settlements:
            raise ValueError(f"{path.name} must contain a non-empty settlements list")
        required = (
            {"contract_code", "delivery_month", "settlement"}
            if kind == "futures"
            else {
                "option_expiry", "underlying_contract",
                "underlying_delivery_month", "strike", "call_put", "settlement",
            }
        )
        for index, row in enumerate(settlements):
            missing = sorted(required - set(row))
            if missing:
                raise ValueError(
                    f"{path.name} settlements[{index}] missing {missing}"
                )
            if row.get("trade_date", trade_date) != trade_date:
                raise ValueError(
                    f"{path.name} settlements[{index}] has wrong trade_date"
                )
            contract_key = (
                "contract_code" if kind == "futures" else "underlying_contract"
            )
            contract = str(row[contract_key])
            parsed = self.product.parse_contract_code(contract)
            if parsed is None:
                raise ValueError(
                    f"{path.name} settlements[{index}] has invalid "
                    f"{contract_key} {contract!r}"
                )
            year, month = parsed
            delivery_key = (
                "delivery_month"
                if kind == "futures" else "underlying_delivery_month"
            )
            expected_delivery = f"{year:04d}-{month:02d}"
            if row[delivery_key] != expected_delivery:
                raise ValueError(
                    f"{path.name} settlements[{index}] {delivery_key} must be "
                    f"{expected_delivery!r}"
                )
            try:
                settlement = float(row["settlement"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path.name} settlements[{index}] has invalid settlement"
                ) from exc
            if settlement < 0:
                raise ValueError(
                    f"{path.name} settlements[{index}] has negative settlement"
                )
            if kind == "options":
                if row["call_put"] not in {"C", "P"}:
                    raise ValueError(
                        f"{path.name} settlements[{index}] call_put must be C or P"
                    )
                try:
                    strike = float(row["strike"])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{path.name} settlements[{index}] has invalid strike"
                    ) from exc
                if strike <= 0:
                    raise ValueError(
                        f"{path.name} settlements[{index}] strike must be positive"
                    )
                expected_expiry = self.product.calendar.option_expiry_date(
                    year, month,
                ).isoformat()
                if row["option_expiry"] != expected_expiry:
                    raise ValueError(
                        f"{path.name} settlements[{index}] option_expiry must be "
                        f"{expected_expiry!r} for {contract}"
                    )
            row.setdefault("trade_date", trade_date)
            row.setdefault("source_id", f"authorized_{self.product.key}_{kind}")
            if kind == "futures":
                row.setdefault("exchange", self.product.exchange)
                row.setdefault("product", self.product.product_code)
                row.setdefault("currency", self.product.currency)
                row.setdefault("price_unit", self.product.price_unit)
            else:
                row.setdefault("exercise_style", self.product.exercise_style)
                row.setdefault("settlement_style", self.product.settlement_style)
                row.setdefault("contract_multiplier", int(self.product.contract_multiplier))
                row.setdefault("price_note", "authorized_exchange_settlement")
        return json.dumps(data, indent=2, sort_keys=True).encode()

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        trade_date = as_of_date.isoformat()
        source = f"authorized_{self.product.key}_market_data"
        self.manifest_db.start_run(run_id, source, trade_date)
        paths = self.required_paths(as_of_date)
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            detail = f"missing authorized market data: {missing}"
            self.manifest_db.complete_run(
                run_id, "failed", 0, 0, 1, 0, notes=detail,
            )
            return {
                "run_id": run_id, "status": "failed", "success": 0,
                "warning": 0, "failure": 1, "skipped": 0, "detail": detail,
            }

        success = skipped = 0
        try:
            for kind, path in zip(("futures", "options"), paths):
                content = self._validate(path, trade_date, kind)
                digest = hashlib.sha256(content).hexdigest()
                if self.manifest_db.sha256_exists(digest):
                    skipped += 1
                    continue
                source_id = f"authorized_{self.product.key}_{kind}"
                raw_path, sha_written, byte_size = self.raw_store.persist(
                    content=content,
                    source_id=source_id,
                    filename=path.name,
                    trade_date=trade_date,
                    source_url=f"authorized-file:{path}",
                    content_type="application/json",
                )
                self.manifest_db.insert_manifest_entry({
                    "entry_id": str(uuid.uuid4()),
                    "source_id": source_id,
                    "raw_path": str(raw_path),
                    "sha256": sha_written,
                    "byte_size": byte_size,
                    "retrieved_at": datetime.now(timezone.utc),
                    "trade_date": trade_date,
                    "source_url": f"authorized-file:{path}",
                    "content_type": "application/json",
                    "collection_run_id": run_id,
                })
                success += 1
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.manifest_db.complete_run(
                run_id, "failed", success, 0, 1, skipped, notes=str(exc),
            )
            return {
                "run_id": run_id, "status": "failed", "success": success,
                "warning": 0, "failure": 1, "skipped": skipped,
                "detail": str(exc),
            }

        self.manifest_db.complete_run(
            run_id, "success", success, 0, 0, skipped,
        )
        return {
            "run_id": run_id, "status": "success", "success": success,
            "warning": 0, "failure": 0, "skipped": skipped,
        }

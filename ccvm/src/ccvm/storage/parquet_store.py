"""
Parquet-based storage for bronze, silver, and gold layers.

Layout:
    data/{layer}/{dataset}/trade_date=YYYY-MM-DD/data.parquet

All writes are atomic (write to .tmp then rename).
Reads return pyarrow.Table; callers convert to pandas/dicts as needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq


class ParquetStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = Path(base_path)

    def _path(self, layer: str, dataset: str, trade_date: str) -> Path:
        return self.base_path / layer / dataset / f"trade_date={trade_date}" / "data.parquet"

    def write(self, layer: str, dataset: str, trade_date: str, table: pa.Table) -> Path:
        dest = self._path(layer, dataset, trade_date)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="snappy")
        tmp.rename(dest)
        return dest

    def read(self, layer: str, dataset: str, trade_date: str) -> pa.Table:
        path = self._path(layer, dataset, trade_date)
        if not path.exists():
            raise FileNotFoundError(f"No {layer}/{dataset} for {trade_date}: {path}")
        return pq.read_table(path)

    def exists(self, layer: str, dataset: str, trade_date: str) -> bool:
        return self._path(layer, dataset, trade_date).exists()

    def list_dates(self, layer: str, dataset: str) -> list[str]:
        base = self.base_path / layer / dataset
        if not base.exists():
            return []
        dates = []
        for p in sorted(base.iterdir()):
            if p.is_dir() and p.name.startswith("trade_date="):
                d = p.name.split("=", 1)[1]
                if (p / "data.parquet").exists():
                    dates.append(d)
        return dates

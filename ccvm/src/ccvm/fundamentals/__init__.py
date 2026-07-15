"""
Fundamentals provider registry (E4).

Fundamentals are the most product-specific part of the pipeline: WTI reads
the EIA Weekly Petroleum Status Report; Henry Hub would read the EIA Weekly
Natural Gas Storage Report; metals have no weekly government report at all.
This registry isolates that variation behind one lookup keyed by the product
profile's `fundamentals_provider` field.

A provider bundles the four stages the pipeline needs:

    collector_cls   raw ingest (collect_day)
    bronze          raw file → bronze table   (parse(path, sha256))
    silver          bronze → silver           (normalize(table, as_of))
    features        silver → gold             (compute(table, as_of))

`get_provider(None)` returns None — the pipeline degrades gracefully to a
fundamentals-less run (agreement already handles missing EIA inputs), which
is the correct behavior for products like gold/copper until a provider is
written for them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FundamentalsProvider:
    name: str
    collector_cls: type
    bronze: object      # module with parse(raw_path, sha256) -> pa.Table
    silver: object      # module with normalize(bronze, as_of) -> pa.Table
    features: object    # module with compute(silver, as_of) -> pa.Table
    source_id_fragment: str   # manifest source_id substring for entry routing
    cadence_note: str


def _eia_weekly_petroleum() -> FundamentalsProvider:
    from ..analytics import eia_features
    from ..collectors.eia import EIACollector
    from ..normalizers import silver_eia
    from ..parsers import bronze_eia
    return FundamentalsProvider(
        name="eia_weekly_petroleum",
        collector_cls=EIACollector,
        bronze=bronze_eia,
        silver=silver_eia,
        features=eia_features,
        source_id_fragment="eia",
        cadence_note="weekly (Wed 10:30 ET), 1-week lag",
    )


_REGISTRY = {
    "eia_weekly_petroleum": _eia_weekly_petroleum,
    # "eia_ng_storage": ...   ← Henry Hub port adds its provider here
}


def get_provider(name: Optional[str]) -> Optional[FundamentalsProvider]:
    """Provider by registry key; None for fundamentals-less products."""
    if not name:
        return None
    factory = _REGISTRY.get(name)
    if factory is None:
        raise KeyError(
            f"Unknown fundamentals provider {name!r} — register it in "
            f"ccvm.fundamentals._REGISTRY (available: {sorted(_REGISTRY)})")
    return factory()

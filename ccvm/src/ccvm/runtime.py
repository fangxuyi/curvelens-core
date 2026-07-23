"""Deployment-scoped runtime paths."""
from __future__ import annotations

import os
import re
from pathlib import Path


def data_dir(product: str | None = None) -> Path:
    """Return an override or ccvm/data/products/<product> runtime root."""
    configured = os.environ.get("CCVM_DATA_DIR")
    if configured:
        active_product = os.environ.get("CCVM_PRODUCT", "wti")
        if product is not None and product != active_product:
            raise ValueError(
                "CCVM_DATA_DIR is a single-product override and cannot serve "
                f"dashboard product {product!r}; active product is {active_product!r}"
            )
        return Path(configured).expanduser().resolve()
    product = product or os.environ.get("CCVM_PRODUCT", "wti")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", product):
        raise ValueError(f"Invalid CCVM_PRODUCT for runtime path: {product!r}")
    return Path(__file__).resolve().parents[2] / "data" / "products" / product

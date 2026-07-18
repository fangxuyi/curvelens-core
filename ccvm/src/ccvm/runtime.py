"""Deployment-scoped runtime paths."""
from __future__ import annotations

import os
import re
from pathlib import Path


def data_dir() -> Path:
    """Return an override or ccvm/data/products/<product> runtime root."""
    configured = os.environ.get("CCVM_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    product = os.environ.get("CCVM_PRODUCT", "wti")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", product):
        raise ValueError(f"Invalid CCVM_PRODUCT for runtime path: {product!r}")
    return Path(__file__).resolve().parents[2] / "data" / "products" / product

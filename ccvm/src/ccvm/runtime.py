"""Deployment-scoped runtime paths."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Return CCVM_DATA_DIR when set, otherwise the legacy ccvm/data path."""
    configured = os.environ.get("CCVM_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "data"

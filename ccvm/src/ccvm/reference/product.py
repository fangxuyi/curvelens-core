"""
Product profile loader (E1) — makes config/markets/<product>.yaml load-bearing.

One deployment = one product. Which product this deployment runs is declared,
not coded: the CCVM_PRODUCT env var (default "wti") selects the YAML profile,
and everything product-specific — contract prefixes, bulletin parsing rules,
expiry-calendar module, fundamentals provider, knowledge pack — is read from
it. Porting to a new commodity = authoring a profile + knowledge pack, not
forking code.

Usage:
    from ccvm.reference.product import get_product
    p = get_product()          # cached singleton for the deployment's product
    p.futures_prefix           # "CL"
    p.calendar.option_expiry_date(2026, 9)   # resolved calendar module
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Optional

import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "markets"


@dataclass(frozen=True)
class BulletinSpec:
    product_header_call: str = "LO CALL"
    product_header_put: str = "LO PUT"
    strike_scale: float = 100.0
    underlying_month_offset: int = 1


@dataclass(frozen=True)
class Product:
    key: str                              # "wti"
    name: str                             # "WTI Crude Oil"
    display_name: str                     # "WTI"
    exchange: str
    product_code: str                     # exchange product code
    currency: str
    price_unit: str                       # "USD/BBL"
    contract_multiplier: float
    tick_size: float
    futures_prefix: str                   # "CL"
    options_prefix: str                   # "LO"
    yfinance_contract_suffix: str         # ".NYM"
    month_codes: dict                     # letter → month number
    calendar_module: str                  # import path for expiry rules
    knowledge_pack: str
    fundamentals_provider: Optional[str]
    futures_depth: int
    options_expiry_depth: int
    bulletin: BulletinSpec = field(default_factory=BulletinSpec)

    @property
    def calendar(self) -> ModuleType:
        """The product's expiry-calendar module (E5 pattern), resolved lazily."""
        return importlib.import_module(self.calendar_module)

    @property
    def month_letters(self) -> dict:
        """month number → letter (inverse of month_codes)."""
        return {v: k for k, v in self.month_codes.items()}

    def parse_contract_code(self, code: str) -> Optional[tuple]:
        """'CLQ26' → (2026, 8) using this product's prefix and month codes."""
        n = len(self.futures_prefix)
        if len(code) < n + 3 or not code.startswith(self.futures_prefix):
            return None
        month = self.month_codes.get(code[n])
        if month is None:
            return None
        try:
            return 2000 + int(code[n + 1:]), month
        except ValueError:
            return None

    def contract_code(self, year: int, month: int) -> str:
        return f"{self.futures_prefix}{self.month_letters[month]}{str(year)[2:]}"


def _load_yaml(key: str) -> dict:
    path = _CONFIG_DIR / f"{key}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No product profile at {path} — set CCVM_PRODUCT to a product with "
            f"a config/markets/<product>.yaml profile")
    m = (yaml.safe_load(path.read_text()) or {}).get("market", {})
    if not m:
        raise ValueError(f"Product profile {path} has no top-level 'market:' mapping")
    return m


def load_product(key: str) -> Product:
    m = _load_yaml(key)
    b = m.get("bulletin", {}) or {}
    return Product(
        key=key,
        name=m.get("name", key.upper()),
        display_name=m.get("display_name", m.get("name", key.upper())),
        exchange=m.get("exchange", ""),
        product_code=m.get("product_code", ""),
        currency=m.get("currency", "USD"),
        price_unit=m.get("price_unit", ""),
        contract_multiplier=float(m.get("contract_multiplier", 1)),
        tick_size=float(m.get("tick_size", 0.01)),
        futures_prefix=m.get("futures_prefix", m.get("product_code", "")),
        options_prefix=m.get("options_prefix", ""),
        yfinance_contract_suffix=m.get("yfinance_contract_suffix", ""),
        month_codes=m.get("month_codes", {}),
        calendar_module=m.get("calendar_module", "ccvm.reference.wti_calendar"),
        knowledge_pack=m.get("knowledge_pack", key),
        fundamentals_provider=m.get("fundamentals_provider"),
        futures_depth=int(m.get("futures_depth", 12)),
        options_expiry_depth=int(m.get("options_expiry_depth", 5)),
        bulletin=BulletinSpec(
            product_header_call=b.get("product_header_call", "LO CALL"),
            product_header_put=b.get("product_header_put", "LO PUT"),
            strike_scale=float(b.get("strike_scale", 100)),
            underlying_month_offset=int(b.get("underlying_month_offset", 1)),
        ),
    )


@lru_cache(maxsize=4)
def get_product(key: Optional[str] = None) -> Product:
    """The deployment's product profile (CCVM_PRODUCT env var, default wti)."""
    return load_product(key or os.environ.get("CCVM_PRODUCT", "wti"))

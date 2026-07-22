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
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Optional

import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "markets"


@dataclass(frozen=True)
class BulletinSpec:
    product_header_call: str
    product_header_put: str
    url: str
    strike_scale: float = 1.0
    underlying_month_offset: int = 0
    underlying_month_map: tuple[tuple[int, int], ...] = ()
    expiry_basis: str = "underlying_month"
    premium_format: str = "decimal"

    def underlying_month(self, option_year: int, option_month: int) -> tuple[int, int]:
        """Map a bulletin option month to its underlying futures month."""
        mapping = dict(self.underlying_month_map)
        if mapping:
            month = mapping[option_month]
            return option_year + (1 if month < option_month else 0), month
        total = option_month + self.underlying_month_offset - 1
        return option_year + total // 12, total % 12 + 1


@dataclass(frozen=True)
class BenchmarkSpec:
    """Optional front-continuous context benchmark for spread monitoring."""

    name: str
    ticker: str
    source_id: str
    filename_prefix: str


@dataclass(frozen=True)
class NewsSpec:
    """Product-scoped RSS sources and relevance terms."""

    keywords: tuple[str, ...] = ()
    sources: tuple[tuple[str, str, str], ...] = ()  # (key, url, display name)


@dataclass(frozen=True)
class MacroSeriesSpec:
    """One public macro series used by an optional product capability."""

    key: str
    series_id: str
    label: str
    units: str
    role: str
    flat_price_sign: int = 0


@dataclass(frozen=True)
class MacroSpec:
    """Profile-driven macro collection and interpretation settings."""

    provider: str
    api_key_env: str
    history_days: int
    series: tuple[MacroSeriesSpec, ...]


@dataclass(frozen=True)
class AnalysisRoleSpec:
    """One profile-driven perspective in the single-operator workflow."""

    key: str
    display_name: str
    mandate: str
    section_keys: tuple[str, ...]
    news_keywords: tuple[str, ...]
    required_checks: tuple[str, ...]
    report_requirements: tuple[str, ...] = ()
    minimum_key_metrics: int = 1


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
    listed_futures_months: tuple[int, ...] = tuple(range(1, 13))
    futures_price_scale: float = 1.0
    fail_strikes_below: int = 2
    pass_strikes_at: int = 5
    settlement_min: float = 0.0
    settlement_max: Optional[float] = None
    exercise_style: str = "American"
    settlement_style: str = "Futures"
    risk_free_rate: float = 0.05
    rnd_quality_gate: bool = False
    option_premium_tick_size: float = 0.01
    rnd_max_projection_ticks: float = 2.0
    bulletin: Optional[BulletinSpec] = None
    benchmark: Optional[BenchmarkSpec] = None
    macro: Optional[MacroSpec] = None
    news: NewsSpec = field(default_factory=NewsSpec)
    analysis_roles: tuple[AnalysisRoleSpec, ...] = ()
    analysis_blocking_sections: tuple[str, ...] = ("futures",)
    analysis_retryable_empty_sections: tuple[str, ...] = ("futures", "options")
    analysis_max_quality_attempts: int = 2
    trigger_definitions: tuple[dict, ...] = ()
    caveats: tuple[str, ...] = ()
    # CFTC Commitments of Traders (B3). Both None → the deployment has no COT
    # feed and the collector skips (e.g. products CFTC doesn't publish).
    cot_contract_market_code: Optional[str] = None   # "067651" (WTI), "088691" (gold)
    cot_contract_label: Optional[str] = None          # "WTI-PHYSICAL NYMEX"

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

    def option_contract_info(
        self, option_year: int, option_month: int,
    ) -> tuple[date, str, str]:
        """Resolve bulletin option month to expiry and underlying contract."""
        if self.bulletin is None:
            raise ValueError(f"Product {self.key!r} has no bulletin configuration")
        underlying_year, underlying_month = self.bulletin.underlying_month(
            option_year, option_month,
        )
        if self.bulletin.expiry_basis == "option_month":
            expiry = self.calendar.option_expiry_for_option_month(
                option_year, option_month,
            )
        elif self.bulletin.expiry_basis == "underlying_month":
            expiry = self.calendar.option_expiry_date(
                underlying_year, underlying_month,
            )
        else:
            raise ValueError(
                f"Unsupported bulletin expiry_basis: {self.bulletin.expiry_basis!r}"
            )
        contract = self.contract_code(underlying_year, underlying_month)
        delivery_month = f"{underlying_year:04d}-{underlying_month:02d}"
        return expiry, contract, delivery_month


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
    required_profile_fields = (
        "name", "exchange", "product_code", "currency", "price_unit",
        "futures_prefix", "month_codes", "calendar_module", "knowledge_pack",
    )
    missing_profile = [name for name in required_profile_fields if not m.get(name)]
    if missing_profile:
        raise ValueError(f"Product profile {key!r} missing: {missing_profile}")
    b = m.get("bulletin", {}) or {}
    benchmark = m.get("benchmark", {}) or {}
    news = m.get("news", {}) or {}
    options = m.get("options", {}) or {}
    option_premium_tick_size = float(
        options.get("premium_tick_size", m.get("tick_size", 0.01))
    )
    rnd_max_projection_ticks = float(options.get("rnd_max_projection_ticks", 2.0))
    if option_premium_tick_size <= 0 or rnd_max_projection_ticks <= 0:
        raise ValueError(
            f"Product profile {key!r} RND premium tick and projection limit must be positive"
        )
    macro = m.get("macro", {}) or {}
    analysis = m.get("analysis", {}) or {}
    listed_futures_months = tuple(
        int(value) for value in m.get("listed_futures_months", range(1, 13))
    )
    if (not listed_futures_months
            or len(set(listed_futures_months)) != len(listed_futures_months)
            or any(month < 1 or month > 12 for month in listed_futures_months)):
        raise ValueError(
            f"Product profile {key!r} listed_futures_months must be unique months 1..12"
        )
    futures_price_scale = float(m.get("futures_price_scale", 1.0))
    if futures_price_scale <= 0:
        raise ValueError(f"Product profile {key!r} futures_price_scale must be positive")
    analysis_roles = tuple(
        AnalysisRoleSpec(
            key=str(role["key"]),
            display_name=str(role.get("display_name", role["key"])),
            mandate=str(role.get("mandate", "")),
            section_keys=tuple(str(v) for v in role.get("section_keys", [])),
            news_keywords=tuple(str(v).lower() for v in role.get("news_keywords", [])),
            required_checks=tuple(str(v) for v in role.get("required_checks", [])),
            report_requirements=tuple(str(v) for v in role.get("report_requirements", [])),
            minimum_key_metrics=int(role.get("minimum_key_metrics", 1)),
        )
        for role in analysis.get("roles", [])
    )
    role_keys = [role.key for role in analysis_roles]
    if len(role_keys) != len(set(role_keys)):
        raise ValueError(f"Product profile {key!r} has duplicate analysis role keys")
    for role in analysis_roles:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", role.key):
            raise ValueError(
                f"Product profile {key!r} has unsafe analysis role key {role.key!r}"
            )
        if not role.mandate or not role.section_keys or not role.required_checks:
            raise ValueError(
                f"Product profile {key!r} analysis role {role.key!r} requires "
                "mandate, section_keys, and required_checks"
            )
        if role.minimum_key_metrics < 1:
            raise ValueError(
                f"Product profile {key!r} analysis role {role.key!r} requires "
                "minimum_key_metrics >= 1"
            )
    bulletin = None
    if b:
        required = ("product_header_call", "product_header_put", "url")
        missing = [name for name in required if not b.get(name)]
        if missing:
            raise ValueError(f"Product profile {key!r} bulletin missing: {missing}")
        month_map = b.get("underlying_month_map", {}) or {}
        expiry_basis = str(b.get("expiry_basis", "underlying_month"))
        if month_map and set(int(k) for k in month_map) != set(range(1, 13)):
            raise ValueError(
                f"Product profile {key!r} underlying_month_map must cover months 1..12"
            )
        if expiry_basis not in {"option_month", "underlying_month"}:
            raise ValueError(
                f"Product profile {key!r} has invalid expiry_basis {expiry_basis!r}"
            )
        bulletin = BulletinSpec(
            product_header_call=b["product_header_call"],
            product_header_put=b["product_header_put"],
            url=b["url"],
            strike_scale=float(b.get("strike_scale", 1)),
            underlying_month_offset=int(b.get("underlying_month_offset", 0)),
            underlying_month_map=tuple(
                sorted((int(k), int(v)) for k, v in month_map.items())
            ),
            expiry_basis=expiry_basis,
            premium_format=str(b.get("premium_format", "decimal")),
        )
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
        calendar_module=m["calendar_module"],
        knowledge_pack=m["knowledge_pack"],
        fundamentals_provider=m.get("fundamentals_provider"),
        futures_depth=int(m.get("futures_depth", 12)),
        options_expiry_depth=int(m.get("options_expiry_depth", 5)),
        listed_futures_months=listed_futures_months,
        futures_price_scale=futures_price_scale,
        fail_strikes_below=int(m.get("fail_strikes_below", 2)),
        pass_strikes_at=int(m.get("warn_strikes_below",
                                  m.get("min_strikes_per_expiry", 5))),
        settlement_min=float(m.get("settlement_min", 0)),
        settlement_max=(float(m["settlement_max"])
                        if m.get("settlement_max") is not None else None),
        exercise_style=options.get("exercise_style", "American"),
        settlement_style=options.get("settlement_style", "Futures"),
        risk_free_rate=float(options.get("risk_free_rate", 0.05)),
        rnd_quality_gate=bool(options.get("rnd_quality_gate", False)),
        option_premium_tick_size=option_premium_tick_size,
        rnd_max_projection_ticks=rnd_max_projection_ticks,
        bulletin=bulletin,
        benchmark=(BenchmarkSpec(
            name=benchmark["name"],
            ticker=benchmark["ticker"],
            source_id=benchmark.get("source_id", f"yfinance_{key}_benchmark"),
            filename_prefix=benchmark.get("filename_prefix", f"{key}_benchmark"),
        ) if benchmark else None),
        macro=(MacroSpec(
            provider=str(macro["provider"]),
            api_key_env=str(macro.get("api_key_env", "FRED_API_KEY")),
            history_days=int(macro.get("history_days", 400)),
            series=tuple(
                MacroSeriesSpec(
                    key=str(key),
                    series_id=str(spec["series_id"]),
                    label=str(spec.get("label", key)),
                    units=str(spec.get("units", "")),
                    role=str(spec.get("role", "context")),
                    flat_price_sign=int(spec.get("flat_price_sign", 0)),
                )
                for key, spec in (macro.get("series", {}) or {}).items()
            ),
        ) if macro else None),
        news=NewsSpec(
            keywords=tuple(str(v).lower() for v in news.get("keywords", [])),
            sources=tuple(
                (str(s["key"]), str(s["url"]), str(s.get("name", s["key"])))
                for s in news.get("sources", [])
            ),
        ),
        analysis_roles=analysis_roles,
        analysis_blocking_sections=tuple(
            str(v) for v in analysis.get("blocking_sections", ["futures"])
        ),
        analysis_retryable_empty_sections=tuple(
            str(v) for v in analysis.get(
                "retryable_empty_sections", ["futures", "options"]
            )
        ),
        analysis_max_quality_attempts=max(
            1, int(analysis.get("max_quality_attempts", 2))
        ),
        trigger_definitions=tuple(m.get("triggers", []) or []),
        caveats=tuple(str(v) for v in m.get("caveats", []) or []),
        cot_contract_market_code=(m.get("cot", {}) or {}).get("contract_market_code"),
        cot_contract_label=(m.get("cot", {}) or {}).get("contract_label"),
    )


@lru_cache(maxsize=4)
def get_product(key: Optional[str] = None) -> Product:
    """The deployment's product profile (CCVM_PRODUCT env var, default wti)."""
    return load_product(key or os.environ.get("CCVM_PRODUCT", "wti"))

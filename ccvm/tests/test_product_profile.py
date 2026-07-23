"""Tests for the product profile loader (E1)."""
from __future__ import annotations

from copy import deepcopy

import pytest

from ccvm.reference.product import get_product, load_product


class TestWTIProfile:
    def test_loads_and_caches(self):
        p = get_product("wti")
        assert p.futures_prefix == "CL" and p.options_prefix == "LO"
        assert p.yfinance_contract_suffix == ".NYM"
        assert p.bulletin.strike_scale == 100
        assert p.bulletin.underlying_month_offset == 1
        assert p.bulletin.expiry_basis == "underlying_month"
        assert p.fundamentals_provider == "eia_weekly_petroleum"
        assert p.cot_contract_market_code == "067651"
        assert p.cot_contract_label == "WTI-PHYSICAL NYMEX"
        assert p.analysis_blocking_sections == ("futures",)
        assert p.analysis_retryable_empty_sections == ("futures", "options")
        assert p.option_premium_tick_size == pytest.approx(0.01)
        assert p.rnd_max_projection_ticks == pytest.approx(2.0)
        assert p.rnd_max_fit_residual_ticks == pytest.approx(2.0)
        assert get_product("wti") is p  # lru cached

    def test_calendar_module_resolves(self):
        from datetime import date
        p = get_product("wti")
        assert p.calendar.option_expiry_date(2026, 9) == date(2026, 8, 17)

    def test_contract_code_roundtrip(self):
        p = get_product("wti")
        assert p.contract_code(2026, 8) == "CLQ26"
        assert p.parse_contract_code("CLQ26") == (2026, 8)
        assert p.parse_contract_code("NGQ26") is None      # wrong prefix
        assert p.parse_contract_code("CLA26") is None      # bad month letter

    def test_option_contract_info_uses_wti_offset(self):
        from datetime import date
        expiry, contract, delivery_month = get_product("wti").option_contract_info(2026, 8)
        assert expiry == date(2026, 8, 17)
        assert contract == "CLU26"
        assert delivery_month == "2026-09"

    def test_unknown_product_raises(self):
        with pytest.raises(FileNotFoundError):
            load_product("plutonium")


class TestFundamentalsRegistry:
    def test_wti_provider_resolves(self):
        from ccvm.fundamentals import get_provider
        prov = get_provider("eia_weekly_petroleum")
        assert prov.collector_cls.__name__ == "EIACollector"
        assert hasattr(prov.bronze, "parse")
        assert hasattr(prov.silver, "normalize")
        assert hasattr(prov.features, "compute")

    def test_none_is_fundamentals_less(self):
        from ccvm.fundamentals import get_provider
        assert get_provider(None) is None

    def test_unknown_provider_raises(self):
        from ccvm.fundamentals import get_provider
        with pytest.raises(KeyError):
            get_provider("astrology")


class TestNonWTIProfile:
    def test_gold_rnd_projection_policy_is_profile_driven(self):
        p = get_product("gold")
        assert p.option_premium_tick_size == pytest.approx(0.10)
        assert p.rnd_max_projection_ticks == pytest.approx(2.0)
        assert p.rnd_max_fit_residual_ticks == pytest.approx(2.0)

    def test_gold_news_uses_targeted_macro_and_market_sources(self):
        p = get_product("gold")
        source_keys = {source[0] for source in p.news.sources}

        assert "federal_reserve_press" not in source_keys
        assert {
            "federal_reserve_monetary_policy",
            "federal_reserve_speeches",
            "federal_reserve_testimony",
            "bls_consumer_prices",
            "bls_producer_prices",
            "bls_employment",
            "bea_releases",
            "cnbc_top_news",
        } <= source_keys
        assert "monetary policy" in p.news.keywords
        assert "consumer price" in p.news.keywords

    def test_rejects_unsafe_analysis_role_key(self, monkeypatch):
        import ccvm.reference.product as product_module

        profile = deepcopy(product_module._load_yaml("gold"))
        profile["analysis"]["roles"][0]["key"] = "../escape"
        monkeypatch.setattr(product_module, "_load_yaml", lambda _key: profile)
        with pytest.raises(ValueError, match="unsafe analysis role key"):
            load_product("unsafe")

    def test_optional_capabilities_do_not_default_to_wti(self, monkeypatch):
        import ccvm.reference.product as product_module

        profile = {
            "name": "Example Metal",
            "display_name": "Metal",
            "exchange": "COMEX",
            "product_code": "GC",
            "currency": "USD",
            "price_unit": "USD/OZT",
            "contract_multiplier": 100,
            "tick_size": 0.1,
            "futures_prefix": "GC",
            "options_prefix": "OG",
            "yfinance_contract_suffix": ".CMX",
            "month_codes": {"G": 2, "J": 4, "M": 6, "Q": 8, "V": 10, "Z": 12},
            "calendar_module": "ccvm.reference.wti_calendar",
            "knowledge_pack": "example_metal",
            "futures_depth": 6,
            "options_expiry_depth": 3,
            "settlement_min": 100,
            "settlement_max": 10000,
        }
        monkeypatch.setattr(product_module, "_load_yaml", lambda _key: profile)
        p = load_product("metal")

        assert p.futures_prefix == "GC"
        assert p.price_unit == "USD/OZT"
        assert p.bulletin is None
        assert p.benchmark is None
        assert p.fundamentals_provider is None
        assert p.news.sources == ()

    def test_yfinance_contracts_use_profile_depth_and_month_codes(self, monkeypatch):
        from datetime import date
        import ccvm.collectors.yfinance_futures as collector_module
        import ccvm.reference.product as product_module

        p = get_product("wti")
        monkeypatch.setattr(collector_module, "get_product", lambda: p)
        contracts = collector_module._active_contracts(date(2026, 7, 16))
        assert len(contracts) == p.futures_depth
        assert contracts[0] == ("CLQ26.NYM", "CLQ26", "2026-08")

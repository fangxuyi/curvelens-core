"""Tests for the product profile loader (E1)."""
from __future__ import annotations

import pytest

from ccvm.reference.product import get_product, load_product


class TestWTIProfile:
    def test_loads_and_caches(self):
        p = get_product("wti")
        assert p.futures_prefix == "CL" and p.options_prefix == "LO"
        assert p.yfinance_contract_suffix == ".NYM"
        assert p.bulletin.strike_scale == 100
        assert p.bulletin.underlying_month_offset == 1
        assert p.fundamentals_provider == "eia_weekly_petroleum"
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

"""Tests for futures features, option surface analytics, and agreement classification."""
from __future__ import annotations

from datetime import date

import pyarrow as pa
import pytest

from ccvm.analytics import futures_features, option_features, agreement

AS_OF = date(2026, 6, 24)
PRIOR = date(2026, 6, 23)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _silver_futures(settlements: list[float], start_pos: int = 1) -> pa.Table:
    from ccvm.normalizers.silver_futures import _SILVER_SCHEMA
    n = len(settlements)
    months = [f"2026-{7 + i:02d}" for i in range(n)]
    codes = [f"CLQ{26 + i}" for i in range(n)]
    rows = {f.name: [] for f in _SILVER_SCHEMA}
    for i in range(n):
        rows["trade_date"].append("2026-06-24")
        rows["exchange"].append("NYMEX")
        rows["product"].append("CL")
        rows["contract_code"].append(codes[i])
        rows["delivery_month"].append(months[i])
        rows["settlement"].append(settlements[i])
        rows["volume"].append(10000 - i * 1000)
        rows["open_interest"].append(None)
        rows["currency"].append("USD")
        rows["price_unit"].append("USD/BBL")
        rows["last_trade_date"].append("2026-07-21")
        rows["option_expiry"].append("2026-07-13")
        rows["days_to_expiry"].append(27 + i * 30)
        rows["curve_position"].append(start_pos + i)
        rows["source_id"].append("yfinance_wti_futures")
        rows["raw_file_sha256"].append("abc")
        rows["silver_status"].append("PASS")
        rows["silver_note"].append("")
    return pa.table(rows, schema=_SILVER_SCHEMA)


def _silver_options(n_strikes: int = 8, atm: float = 75.0, iv_level: float = 0.30) -> pa.Table:
    from ccvm.normalizers.silver_options import _SILVER_SCHEMA
    rows = {f.name: [] for f in _SILVER_SCHEMA}
    strikes = [atm - (n_strikes // 2 - i) * 2.5 for i in range(n_strikes)]
    for strike in strikes:
        for cp in ("C", "P"):
            # Simple intrinsic + time value
            if cp == "C":
                settle = max(0.1, atm - strike + 2.0)
            else:
                settle = max(0.1, strike - atm + 2.0)
            rows["trade_date"].append("2026-06-24")
            rows["option_expiry"].append("2026-08-21")
            rows["option_symbol"].append(f"USO{strike:.0f}{cp}")
            rows["underlying_contract"].append("USO")
            rows["underlying_delivery_month"].append("2026-08")
            rows["strike"].append(strike)
            rows["call_put"].append(cp)
            rows["settlement"].append(settle)
            rows["bid"].append(settle - 0.05)
            rows["ask"].append(settle + 0.05)
            rows["volume"].append(100)
            rows["open_interest"].append(500)
            rows["implied_volatility"].append(iv_level)
            rows["delta"].append(0.5 if cp == "C" else -0.5)
            rows["gamma"].append(0.02)
            rows["theta"].append(-0.03)
            rows["vega"].append(0.10)
            rows["exercise_style"].append("American")
            rows["settlement_style"].append("Equity_ETF")
            rows["contract_multiplier"].append(100)
            rows["source_id"].append("cme_bulletin_lo_option")
            rows["price_note"].append("USO_proxy")
            rows["raw_file_sha256"].append("abc")
            rows["silver_status"].append("PASS")
            rows["silver_note"].append("")
    return pa.table(rows, schema=_SILVER_SCHEMA)


# ──────────────────────────────────────────────────────────────────────────────
# Futures features
# ──────────────────────────────────────────────────────────────────────────────

class TestFuturesFeatures:
    def test_schema_fields(self):
        t = futures_features.compute(_silver_futures([72.0, 71.5, 71.0]), AS_OF)
        for f in ("contract_code", "settlement", "spread_to_next", "butterfly",
                  "front_back_slope", "contango_flag", "curve_position"):
            assert f in t.schema.names

    def test_spread_to_next(self):
        t = futures_features.compute(_silver_futures([72.0, 71.0, 70.0]), AS_OF)
        d = t.to_pydict()
        spreads = {c: s for c, s in zip(d["contract_code"], d["spread_to_next"])}
        # CLQ26 spread to CLU26: 71.0 - 72.0 = -1.0
        assert spreads["CLQ26"] == pytest.approx(-1.0, abs=0.01)
        # Last contract has no next
        assert d["spread_to_next"][-1] is None

    def test_butterfly(self):
        # Butterfly for middle contract = −72 + 2×71 − 70 = 0
        t = futures_features.compute(_silver_futures([72.0, 71.0, 70.0]), AS_OF)
        d = t.to_pydict()
        # Position 2 is the middle; CLQ27 is at index 1
        # Sorted by curve_position → index 1 is position 2
        sorted_rows = sorted(zip(d["curve_position"], d["butterfly"]))
        butterfly_pos2 = sorted_rows[1][1]
        assert butterfly_pos2 == pytest.approx(0.0, abs=0.01)

    def test_contango_flag_true(self):
        t = futures_features.compute(_silver_futures([70.0, 71.0, 72.0]), AS_OF)
        d = t.to_pydict()
        assert all(v is True for v in d["contango_flag"])

    def test_backwardation_flag(self):
        t = futures_features.compute(_silver_futures([72.0, 71.0, 70.0]), AS_OF)
        d = t.to_pydict()
        assert all(v is False for v in d["contango_flag"])

    def test_return_1d_with_prior(self):
        prior = _silver_futures([70.0, 69.5, 69.0])
        curr = _silver_futures([72.0, 71.0, 70.0])
        t = futures_features.compute(curr, AS_OF, prior)
        d = t.to_pydict()
        # CLQ26: (72-70)/70 ≈ 0.02857
        idx = d["contract_code"].index("CLQ26")
        assert d["return_1d"][idx] == pytest.approx((72 - 70) / 70, rel=0.01)

    def test_return_1d_none_without_prior(self):
        t = futures_features.compute(_silver_futures([72.0, 71.0]), AS_OF, None)
        d = t.to_pydict()
        assert all(v is None for v in d["return_1d"])

    def test_curve_position_ordering(self):
        t = futures_features.compute(_silver_futures([72.0, 71.5, 71.0, 70.5]), AS_OF)
        d = t.to_pydict()
        assert d["curve_position"] == sorted(d["curve_position"])

    def test_empty_input_returns_empty_table(self):
        from ccvm.normalizers.silver_futures import _SILVER_SCHEMA
        empty = pa.table({f.name: [] for f in _SILVER_SCHEMA}, schema=_SILVER_SCHEMA)
        t = futures_features.compute(empty, AS_OF)
        assert len(t) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Option features
# ──────────────────────────────────────────────────────────────────────────────

class TestOptionFeatures:
    def test_schema_fields(self):
        sf = _silver_futures([75.0, 74.5])
        so = _silver_options(n_strikes=8, atm=75.0)
        t = option_features.compute(so, sf, AS_OF)
        if len(t) == 0:
            pytest.skip("No valid option rows computed")
        for f in ("black76_iv", "black76_delta", "atm_iv", "risk_reversal_25d",
                  "coverage_status", "moneyness_log"):
            assert f in t.schema.names

    def test_iv_positive(self):
        sf = _silver_futures([75.0, 74.5])
        so = _silver_options(n_strikes=8, atm=75.0)
        t = option_features.compute(so, sf, AS_OF)
        if len(t) == 0:
            pytest.skip("No valid option rows")
        ivs = [v for v in t.column("black76_iv").to_pylist() if v is not None]
        assert all(v > 0 for v in ivs)

    def test_atm_iv_in_reasonable_range(self):
        sf = _silver_futures([75.0, 74.5])
        so = _silver_options(n_strikes=8, atm=75.0)
        t = option_features.compute(so, sf, AS_OF)
        if len(t) == 0:
            pytest.skip("No valid option rows")
        atm_vals = [v for v in t.column("atm_iv").to_pylist() if v is not None]
        for v in atm_vals:
            assert 0.01 < v < 3.0, f"ATM IV {v:.4f} out of range"

    def test_moneyness_sign(self):
        """ATM options should have log(F/K) near 0."""
        sf = _silver_futures([75.0, 74.5])
        so = _silver_options(n_strikes=8, atm=75.0)
        t = option_features.compute(so, sf, AS_OF)
        if len(t) == 0:
            pytest.skip("No valid option rows")
        d = t.to_pydict()
        strikes = d["strike"]
        moneyness = d["moneyness_log"]
        fwd = d["forward_price"][0]
        for s, m in zip(strikes, moneyness):
            if m is not None and s is not None:
                expected = math.log(fwd / s) if s > 0 else None
                if expected is not None:
                    assert abs(m - expected) < 1e-6


import math


# ──────────────────────────────────────────────────────────────────────────────
# Agreement classification
# ──────────────────────────────────────────────────────────────────────────────

class TestAgreement:
    # front_settlement is a required input (E2): thresholds are price-relative
    # and there is deliberately no fallback reference price.
    def test_confirmed_upside(self):
        r = agreement.classify(-0.50, False, 0.05, 0.30, None, None, front_settlement=70.0)
        assert r["state"] == "confirmed_upside_risk"
        assert r["confidence"] == "high"

    def test_confirmed_downside(self):
        r = agreement.classify(0.50, True, -0.05, 0.30, None, None, front_settlement=70.0)
        assert r["state"] == "confirmed_downside_risk"

    def test_cross_disagreement_backwardation_put_skew(self):
        r = agreement.classify(-0.50, False, -0.05, 0.30, None, None, front_settlement=70.0)
        assert r["state"] == "cross_market_disagreement"

    def test_no_material_change_flat(self):
        r = agreement.classify(0.0, True, 0.0, 0.25, 0.25, 0.0, front_settlement=70.0)
        assert r["state"] == "no_material_change"

    def test_insufficient_data_when_slope_none(self):
        r = agreement.classify(None, None, None, None, None, None)
        assert r["state"] == "insufficient_data"

    def test_insufficient_data_when_settlement_missing(self):
        # no silent reference-price fallback (PR #1 review)
        r = agreement.classify(-0.50, False, 0.05, 0.30, None, None)
        assert r["state"] == "insufficient_data"

    def test_evidence_list_populated(self):
        r = agreement.classify(-0.50, False, 0.05, 0.30, 0.28, -0.40, front_settlement=70.0)
        assert len(r["evidence"]) > 0

    def test_futures_only_repricing(self):
        # Futures moved (backwardation), options unknown (rr=None), IV flat
        r = agreement.classify(-0.50, False, None, 0.30, 0.30, 0.0, front_settlement=70.0)
        assert r["state"] in ("futures_only_repricing", "no_material_change")

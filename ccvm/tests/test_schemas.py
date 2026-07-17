from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from ccvm.schemas import (
    CatalystEvent,
    CollectionRun,
    FundamentalObservation,
    FuturesSettlement,
    ManifestEntry,
    OptionSettlement,
)

NOW = datetime.now(timezone.utc)
SHA = "a" * 64


# ---------------------------------------------------------------------------
# FuturesSettlement
# ---------------------------------------------------------------------------

def make_futures(**kwargs) -> dict:
    base = dict(
        trade_date=date(2024, 1, 2),
        exchange="NYMEX",
        product="CL",
        contract_code="CLG24",
        delivery_month="2024-02",
        settlement=72.70,
        volume=285000,
        open_interest=443521,
        currency="USD",
        price_unit="USD/BBL",
        source_id="test",
        retrieved_at=NOW,
        raw_file_sha256=SHA,
    )
    base.update(kwargs)
    return base


def test_futures_settlement_valid():
    fs = FuturesSettlement(**make_futures())
    assert fs.settlement == 72.70
    assert fs.delivery_month == "2024-02"


def test_futures_settlement_zero_settlement_rejected():
    with pytest.raises(ValidationError):
        FuturesSettlement(**make_futures(settlement=0.0))


def test_futures_settlement_negative_settlement_rejected():
    with pytest.raises(ValidationError):
        FuturesSettlement(**make_futures(settlement=-1.0))


def test_futures_settlement_bad_delivery_month_format():
    with pytest.raises(ValidationError):
        FuturesSettlement(**make_futures(delivery_month="202402"))


def test_futures_settlement_bad_delivery_month_month_13():
    with pytest.raises(ValidationError):
        FuturesSettlement(**make_futures(delivery_month="2024-13"))


def test_futures_natural_key():
    fs = FuturesSettlement(**make_futures())
    key = fs.natural_key()
    assert key == (date(2024, 1, 2), "NYMEX", "CL", "CLG24", "test")


def test_futures_optional_fields_default():
    fs = FuturesSettlement(**make_futures(volume=None, open_interest=None))
    assert fs.volume is None
    assert fs.currency == "USD"


# ---------------------------------------------------------------------------
# OptionSettlement
# ---------------------------------------------------------------------------

def make_option(**kwargs) -> dict:
    base = dict(
        trade_date=date(2024, 1, 2),
        option_expiry=date(2024, 1, 16),
        underlying_contract="CLG24",
        underlying_delivery_month="2024-02",
        strike=72.0,
        call_put="C",
        settlement=2.10,
        volume=4100,
        open_interest=22300,
        source_id="test",
        retrieved_at=NOW,
        raw_file_sha256=SHA,
    )
    base.update(kwargs)
    return base


def test_option_settlement_valid():
    opt = OptionSettlement(**make_option())
    assert opt.call_put == "C"
    assert opt.strike == 72.0


def test_option_expiry_on_trade_date_rejected():
    with pytest.raises(ValidationError):
        OptionSettlement(**make_option(option_expiry=date(2024, 1, 2)))


def test_option_expiry_before_trade_date_rejected():
    with pytest.raises(ValidationError):
        OptionSettlement(**make_option(option_expiry=date(2024, 1, 1)))


def test_option_negative_strike_rejected():
    with pytest.raises(ValidationError):
        OptionSettlement(**make_option(strike=-5.0))


def test_option_negative_settlement_rejected():
    with pytest.raises(ValidationError):
        OptionSettlement(**make_option(settlement=-0.01))


def test_option_zero_settlement_allowed():
    opt = OptionSettlement(**make_option(settlement=0.0))
    assert opt.settlement == 0.0


def test_option_natural_key():
    opt = OptionSettlement(**make_option())
    key = opt.natural_key()
    assert key == (date(2024, 1, 2), date(2024, 1, 16), "CLG24", 72.0, "C", "test")


# ---------------------------------------------------------------------------
# FundamentalObservation
# ---------------------------------------------------------------------------

def test_fundamental_observation_valid():
    obs = FundamentalObservation(
        series_id="PET.WCRSTUS1.W",
        period="2024-01-05",
        release_timestamp=NOW,
        vintage_timestamp=NOW,
        value=432.1,
        unit="million barrels",
        geography="US",
        source_id="eia_api_v2",
        retrieved_at=NOW,
    )
    assert obs.value == 432.1


# ---------------------------------------------------------------------------
# CatalystEvent
# ---------------------------------------------------------------------------

def test_catalyst_event_valid():
    ev = CatalystEvent(
        event_id=CatalystEvent.make_event_id("outage", "Gulf platform outage", "2024-01-02T00:00:00Z", "news"),
        event_type="outage",
        title="Gulf platform outage",
        published_at=NOW,
        direction="bullish_supply",
        magnitude="medium",
        source_id="news",
        evidence=["Platform A shut in for repairs"],
    )
    assert ev.magnitude == "medium"
    assert len(ev.event_id) == 16


def test_catalyst_event_id_stable():
    id1 = CatalystEvent.make_event_id("outage", "Title", "2024-01-02", "src")
    id2 = CatalystEvent.make_event_id("outage", "Title", "2024-01-02", "src")
    assert id1 == id2


# ---------------------------------------------------------------------------
# ManifestEntry and CollectionRun
# ---------------------------------------------------------------------------

def test_manifest_entry_valid():
    entry = ManifestEntry(
        source_id="test",
        raw_path="/tmp/test.csv",
        sha256=SHA,
        byte_size=1024,
        retrieved_at=NOW,
        collection_run_id="run-1",
    )
    assert entry.byte_size == 1024
    assert len(entry.entry_id) == 36  # UUID format


def test_collection_run_valid():
    run = CollectionRun(
        source_id="test",
        as_of_date="2024-01-02",
        started_at=NOW,
        status="running",
    )
    assert run.success_count == 0
    assert run.status == "running"

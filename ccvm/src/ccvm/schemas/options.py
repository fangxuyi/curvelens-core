from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator


class OptionSettlement(BaseModel):
    trade_date: date
    option_symbol: Optional[str] = None
    option_expiry: date
    underlying_contract: str
    underlying_delivery_month: str  # YYYY-MM
    strike: float
    call_put: Literal["C", "P"]
    settlement: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    exercise_style: str = "American"
    settlement_style: str = "Futures"
    contract_multiplier: int = 1000
    source_id: str
    price_note: Optional[str] = None
    retrieved_at: datetime
    raw_file_sha256: str

    @field_validator("underlying_delivery_month")
    @classmethod
    def validate_delivery_month(cls, v: str) -> str:
        if not re.match(r"^\d{4}-(?:0[1-9]|1[0-2])$", v):
            raise ValueError(f"underlying_delivery_month must be YYYY-MM, got {v!r}")
        return v

    @field_validator("strike")
    @classmethod
    def validate_strike(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"strike must be positive, got {v}")
        return v

    @field_validator("settlement")
    @classmethod
    def validate_settlement(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"settlement must be non-negative, got {v}")
        return v

    @model_validator(mode="after")
    def expiry_after_trade_date(self) -> OptionSettlement:
        if self.option_expiry <= self.trade_date:
            raise ValueError(
                f"option_expiry {self.option_expiry} must be after trade_date {self.trade_date}"
            )
        return self

    def natural_key(self) -> tuple:
        return (
            self.trade_date,
            self.option_expiry,
            self.underlying_contract,
            self.strike,
            self.call_put,
            self.source_id,
        )

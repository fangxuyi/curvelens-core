from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


class FuturesSettlement(BaseModel):
    trade_date: date
    exchange: str
    product: str
    contract_code: str
    delivery_month: str  # YYYY-MM
    settlement: float
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    currency: str
    price_unit: str
    source_id: str
    source_record_id: Optional[str] = None
    retrieved_at: datetime
    raw_file_sha256: str

    @field_validator("delivery_month")
    @classmethod
    def validate_delivery_month(cls, v: str) -> str:
        if not re.match(r"^\d{4}-(?:0[1-9]|1[0-2])$", v):
            raise ValueError(f"delivery_month must be YYYY-MM format, got {v!r}")
        return v

    @field_validator("settlement")
    @classmethod
    def validate_settlement(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"settlement must be positive, got {v}")
        return v

    @field_validator("volume", "open_interest")
    @classmethod
    def validate_non_negative_int(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError(f"value must be non-negative, got {v}")
        return v

    def natural_key(self) -> tuple:
        return (self.trade_date, self.exchange, self.product, self.contract_code, self.source_id)

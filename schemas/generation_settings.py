from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.enums import FeeType, LateFeeType


class GenerationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_day: int = Field(ge=1, le=31, strict=True)
    late_fee_enabled: bool = Field(default=False, strict=True)
    late_fee_type: LateFeeType | None = None
    late_fee_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    min_due_amount: int | None = Field(
        default=None, ge=1, le=100, strict=True, description="Minimum due percentage"
    )


PaymentDueDays = Annotated[int, Field(ge=0, le=2147483647, strict=True)]
PaymentDueDay = Annotated[int, Field(ge=1, le=31, strict=True)]


class GenerationSettingsWrite(GenerationBase):
    @model_validator(mode="after")
    def require_late_fee_fields(self):
        if self.late_fee_enabled and (self.late_fee_amount is None or self.min_due_amount is None):
            raise ValueError("late_fee_amount and min_due_amount are required when late fees are enabled")
        return self


class FeeSettingsWrite(GenerationSettingsWrite):
    fee_type: Literal[FeeType.SCHOOL_FEE] = FeeType.SCHOOL_FEE
    payment_due_days: PaymentDueDays


class TransportSettingsWrite(GenerationSettingsWrite):
    fee_type: Literal[FeeType.TRANSPORT] = FeeType.TRANSPORT
    payment_due_day: PaymentDueDay


class FeeSettingsResponse(GenerationBase):
    fee_type: Literal[FeeType.SCHOOL_FEE] = FeeType.SCHOOL_FEE
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int
    created_at: date | None
    updated_at: date | None
    payment_due_days: PaymentDueDays


class TransportSettingsResponse(GenerationBase):
    fee_type: Literal[FeeType.TRANSPORT] = FeeType.TRANSPORT
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int
    created_at: date | None
    updated_at: date | None
    payment_due_day: PaymentDueDay


class FeeSettingsResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeSettingsResponse


class TransportSettingsResult(BaseModel):
    status: str = "success"
    message: str
    data: TransportSettingsResponse

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
    fee_type: FeeType
    payment_due_days: PaymentDueDays | None = None
    payment_due_day: PaymentDueDay | None = None
    school_id: int | None = Field(
        default=None, gt=0, strict=True, description="Must match the school_id in the request URL."
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_payment_due_field(cls, data):
        if not isinstance(data, dict):
            return data

        fee_type = data.get("fee_type", cls.model_fields["fee_type"].default)
        if fee_type == FeeType.SCHOOL_FEE:
            expected, alternate = "payment_due_days", "payment_due_day"
        elif fee_type == FeeType.TRANSPORT:
            expected, alternate = "payment_due_day", "payment_due_days"
        else:
            return data

        if data.get(expected) is None and data.get(alternate) is not None:
            data = data.copy()
            data[expected] = data.pop(alternate)
        return data

    @model_validator(mode="after")
    def require_late_fee_fields(self):
        if self.late_fee_enabled and (self.late_fee_amount is None or self.min_due_amount is None):
            raise ValueError("late_fee_amount and min_due_amount are required when late fees are enabled")
        if self.fee_type == FeeType.SCHOOL_FEE:
            if self.payment_due_days is None or self.payment_due_day is not None:
                raise ValueError("school_fee requires payment_due_days")
        elif self.payment_due_day is None or self.payment_due_days is not None:
            raise ValueError("transport requires payment_due_day")
        return self


class FeeSettingsWrite(GenerationSettingsWrite):
    fee_type: FeeType = FeeType.SCHOOL_FEE


class TransportSettingsWrite(GenerationSettingsWrite):
    fee_type: FeeType = FeeType.TRANSPORT


class FeeSettingsResponse(GenerationBase):
    fee_type: Literal[FeeType.SCHOOL_FEE] = FeeType.SCHOOL_FEE
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int
    session_id: int
    created_at: date | None
    updated_at: date | None
    payment_due_days: PaymentDueDays


class TransportSettingsResponse(GenerationBase):
    fee_type: Literal[FeeType.TRANSPORT] = FeeType.TRANSPORT
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int
    session_id: int
    created_at: date | None
    updated_at: date | None
    payment_due_day: PaymentDueDay


class GenerationSettingsResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeSettingsResponse | TransportSettingsResponse


class GenerationSettingsListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[FeeSettingsResponse | TransportSettingsResponse]

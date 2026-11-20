from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from schemas.enums import FeeType, LateFeeType


class GenerationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_day: int = Field(ge=1, le=31, strict=True)
    late_fee_enabled: bool = Field(default=True, strict=True)
    late_fee_type: LateFeeType | None = None
    late_fee_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    min_due_amount: int | None = Field(
        default=None, ge=1, le=100, strict=True, description="Minimum due percentage"
    )


class GenerationSettingsWrite(GenerationBase):
    fee_type: FeeType
    payment_due_day: int = Field(
        ge=0,
        le=2147483647,
        strict=True,
        validation_alias=AliasChoices("payment_due_day", "payment_due_days"),
        description="Days after generation for school fees, or day of month (1–31) for transport.",
    )
    school_id: int | None = Field(
        default=None, gt=0, strict=True, description="Must match the school_id in the request URL."
    )

    @model_validator(mode="after")
    def validate_settings(self):
        if (
            self.late_fee_enabled
            and "late_fee_enabled" in self.model_fields_set
            and (self.late_fee_amount is None or self.min_due_amount is None)
        ):
            raise ValueError("late_fee_amount and min_due_amount are required when late fees are enabled")
        if self.fee_type == FeeType.TRANSPORT and not 1 <= self.payment_due_day <= 31:
            raise ValueError("transport payment_due_day must be between 1 and 31")
        return self


class FeeSettingsWrite(GenerationSettingsWrite):
    fee_type: FeeType = FeeType.SCHOOL_FEE


class TransportSettingsWrite(GenerationSettingsWrite):
    fee_type: FeeType = FeeType.TRANSPORT


class FeeSettingsResponse(GenerationBase):
    fee_type: Literal[FeeType.SCHOOL_FEE]
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int
    session_id: int
    created_at: date | None
    updated_at: date | None
    payment_due_day: int


class TransportSettingsResponse(GenerationBase):
    fee_type: Literal[FeeType.TRANSPORT]
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int
    session_id: int
    created_at: date | None
    updated_at: date | None
    payment_due_day: int


class GenerationSettingsResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeSettingsResponse | TransportSettingsResponse


class SettingsByFeeType(BaseModel):
    school_fee: FeeSettingsResponse | None = None
    transport: TransportSettingsResponse | None = None


class GenerationSettingsByFeeTypeResult(BaseModel):
    status: str = "success"
    message: str
    data: SettingsByFeeType

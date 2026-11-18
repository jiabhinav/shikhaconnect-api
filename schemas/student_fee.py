from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from schemas.enums import FeeAdjustmentFrequency, FeeDiscountType, PaymentMode, StudentFeeStatus


class StudentFeeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: int = Field(gt=0)
    months: list["StudentFeeMonthPayment"] = Field(min_length=1, max_length=12)
    payment_mode: PaymentMode | None = None
    description: str | None = Field(default=None, max_length=1000)
    collected_by: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_unique_months(self):
        if len({month.fee_month for month in self.months}) != len(self.months):
            raise ValueError("fee_month values must be unique")
        return self


class StudentFeeMonthPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fee_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    payment_amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)

class StudentFeeHeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fee_head_id: int
    fee_head_name: str
    amount: Decimal
    discount_amount: Decimal
    fine_amount: Decimal
    net_amount: Decimal
    paid_amount: Decimal

    @computed_field
    @property
    def due_amount(self) -> Decimal:
        return self.net_amount - self.paid_amount


class StudentFeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    school_id: int
    academic_session_id: int
    student_id: int
    fee_month: str
    total_amount: Decimal
    discount_amount: Decimal
    fine_amount: Decimal
    net_amount: Decimal
    paid_amount: Decimal
    status: StudentFeeStatus
    payment_mode: PaymentMode | None
    description: str | None
    collected_by: str | None
    fee_heads: list[StudentFeeHeadResponse]

    @computed_field
    @property
    def due_amount(self) -> Decimal:
        return self.net_amount - self.paid_amount


class StudentFeeResult(BaseModel):
    status: str = "success"
    message: str
    data: StudentFeeResponse


class StudentFeeListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    data: list[StudentFeeResponse]


class FeePaymentHeadStatus(BaseModel):
    fee_head_id: int | None
    fee_head_name: str
    tag: str | None = None
    source_id: int | None = None
    frequency: FeeAdjustmentFrequency | None = None
    payable_amount: Decimal
    paid_amount: Decimal
    due_amount: Decimal


class FeePaymentAdjustmentStatus(FeePaymentHeadStatus):
    date: date
    discount_type: FeeDiscountType | None


class FeePaymentMonthStatus(BaseModel):
    fee_month: str
    payable_amount: Decimal
    paid_amount: Decimal
    due_amount: Decimal
    status: StudentFeeStatus
    fee_adjustment: list[FeePaymentAdjustmentStatus]
    carry_forward: FeePaymentHeadStatus | None
    fee_heads: list[FeePaymentHeadStatus]

    @computed_field
    @property
    def total_due_amount(self) -> Decimal:
        return self.due_amount


class FeePaymentStatusListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    data: list[FeePaymentMonthStatus]

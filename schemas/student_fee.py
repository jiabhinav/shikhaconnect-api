from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from schemas.enums import FeeAdjustmentFrequency, FeeDiscountType, LateFeeType, PaymentMode, StudentFeeStatus


class StudentFeeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: int = Field(gt=0)
    months: list["StudentFeeMonthPayment | StudentFeeMonthSelection"] = Field(max_length=12)
    payment_amount: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=2)
    late_fee_payment_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=16, decimal_places=2)
    payment_mode: PaymentMode | None = None
    cheque_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    description: str | None = Field(default=None, max_length=1000)
    collected_by: str | None = Field(default=None, max_length=255)

    @field_validator("cheque_number", mode="before")
    @classmethod
    def normalize_blank_cheque_number(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_unique_months(self):
        if not self.months:
            raise ValueError("Select at least one fee month")
        if len({month.fee_month for month in self.months}) != len(self.months):
            raise ValueError("fee_month values must be unique")
        if self.late_fee_payment_amount and any(month.late_fee_payment_amount for month in self.months):
            raise ValueError("Enter late_fee_payment_amount at the top level or on each applicable month")
        if self.payment_amount is not None:
            if not self.months or any(isinstance(month, StudentFeeMonthPayment) for month in self.months):
                raise ValueError("A total payment amount requires selected months without per-month payment amounts")
        elif any(not isinstance(month, StudentFeeMonthPayment) for month in self.months):
            raise ValueError("Enter a total payment amount or a payment amount for each selected month")
        if self.payment_mode == PaymentMode.CHEQUE:
            if self.cheque_number is None:
                raise PydanticCustomError("cheque_number_required", "Enter the cheque number.")
            if self.payment_amount is None and any(
                month.payment_amount + self.late_fee_payment_for(month) <= 0 for month in self.months
            ):
                raise ValueError("cheque payment amounts must be greater than zero")
        elif self.cheque_number is not None:
            raise ValueError("cheque_number is only allowed for cheque payments")
        if self.payment_mode == PaymentMode.RAZORPAY:
            raise ValueError("Razorpay payments require checkout verification")
        return self

    @property
    def total_late_fee_payment(self) -> Decimal:
        return sum((month.late_fee_payment_amount for month in self.months), self.late_fee_payment_amount)

    def late_fee_payment_for(self, month: "StudentFeeMonthSelection") -> Decimal:
        if self.late_fee_payment_amount and month.fee_month == min(entry.fee_month for entry in self.months):
            return self.late_fee_payment_amount
        return month.late_fee_payment_amount


class StudentFeeMonthSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fee_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    late_fee_payment_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=16, decimal_places=2)


class StudentFeeMonthPayment(StudentFeeMonthSelection):
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
    late_fee_amount: Decimal
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


class StudentFeePaymentResult(StudentFeeListResult):
    carry_forward_paid_amount: Decimal
    penalty_paid_amount: Decimal
    late_fee_paid_amount: Decimal


ChequePaymentStatus = Literal["PENDING", "SUCCESS", "BOUNCED"]


class StudentFeeChequeStatusWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["SUCCESS", "BOUNCED"]


class StudentFeeChequeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    school_id: int
    session_id: int
    student_id: int
    cheque_number: str
    payment_mode: Literal[PaymentMode.CHEQUE] = PaymentMode.CHEQUE
    status: ChequePaymentStatus
    months: list[StudentFeeMonthPayment]
    late_fee_payment_amount: Decimal
    description: str | None
    collected_by: str | None
    created_at: datetime
    resolved_at: datetime | None

    @computed_field
    @property
    def payment_amount(self) -> Decimal:
        return sum(
            (entry.payment_amount + entry.late_fee_payment_amount for entry in self.months),
            self.late_fee_payment_amount,
        )


class StudentFeeChequeResult(BaseModel):
    status: str = "success"
    message: str
    data: StudentFeeChequeResponse


class StudentFeeChequeListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    data: list[StudentFeeChequeResponse]


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
    late_fee_paid_amount: Decimal = Decimal("0.00")
    due_amount: Decimal
    status: StudentFeeStatus
    fee_adjustment: list[FeePaymentAdjustmentStatus]
    carry_forward: FeePaymentHeadStatus | None
    fee_heads: list[FeePaymentHeadStatus]

    @computed_field
    @property
    def total_due_amount(self) -> Decimal:
        return self.due_amount


class FeePaymentLateFeeSettings(BaseModel):
    enabled: bool = False
    type: LateFeeType | None = None
    late_fee_amount: Decimal | None = None
    min_due_percentage: int | None = None
    generation_day: int | None = None
    payment_due_day: int | None = None


class FeePaymentStatusListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    data: list[FeePaymentMonthStatus]
    late_fee: FeePaymentLateFeeSettings

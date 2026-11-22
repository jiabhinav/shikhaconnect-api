from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, AliasChoices, BaseModel, ConfigDict, Field, StringConstraints, computed_field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from schemas.enums import FeeAdjustmentFrequency, FeeAdjustmentStatus, FeeDiscountType, LateFeeType, PaymentMode, StudentFeeStatus

from utils.dates import payment_timezone


def display_payment_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(payment_timezone())


PaymentTimestamp = Annotated[datetime, AfterValidator(display_payment_timestamp)]


def parse_imported_payment_date(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if type(value) in (int, float) and 1 <= value < 2958465:
        return datetime(1899, 12, 30) + timedelta(days=value)
    if isinstance(value, str):
        value = value.strip()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            value = value.replace("/", "-").replace(".", "-")
        for date_format in ("%d-%m-%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y %H:%M:%S"):
            try:
                return datetime.strptime(value, date_format)
            except ValueError:
                continue
    raise ValueError("Enter a valid payment date or datetime")


def calculate_student_fee_status(net_amount: Decimal, paid_amount: Decimal) -> StudentFeeStatus:
    if paid_amount >= net_amount:
        return StudentFeeStatus.PAID
    if paid_amount == 0:
        return StudentFeeStatus.UNPAID
    return StudentFeeStatus.PARTIAL


class StudentFeePaymentDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class StudentFeePaymentFields(StudentFeePaymentDetails):
    months: list["StudentFeeMonthPayment | StudentFeeMonthSelection"] = Field(max_length=12)
    payment_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=16, decimal_places=2,
        validation_alias=AliasChoices("payment_amount", "payable_amount"),
        description=(
            "Cash amount after discounts, including any carry-forward and penalties. "
            "The payable_amount alias includes supplied late fees by default."
        ),
    )
    payment_amount_includes_late_fee: bool = Field(
        default=False, description="Treat the top-level payment_amount as the total collected, including the supplied late fees.",
    )
    settle_in_full: bool = Field(
        default=False,
        description="Require the submitted cash amount to cover the full balance of the selected months.",
    )
    @model_validator(mode="before")
    @classmethod
    def interpret_payable_amount_as_total_collected(cls, values):
        if isinstance(values, dict) and "payable_amount" in values:
            return {"payment_amount_includes_late_fee": True, **values}
        return values

    @model_validator(mode="after")
    def validate_unique_months(self):
        if not self.months:
            raise ValueError("Select at least one fee month")
        if len({month.fee_month for month in self.months}) != len(self.months):
            raise ValueError("fee_month values must be unique")
        if self.late_fee_payment_amount and any(month.late_fee_payment_amount for month in self.months):
            raise ValueError("Enter late_fee_payment_amount at the top level or on each applicable month")
        if self.payment_amount_includes_late_fee:
            if self.payment_amount is None:
                raise ValueError("A payment amount that includes late fees must be entered at the top level")
            if self.total_late_fee_payment > self.payment_amount:
                raise ValueError("Late fee payment cannot exceed the total amount collected")
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

    @property
    def regular_payment_amount(self) -> Decimal | None:
        if self.payment_amount_includes_late_fee and self.payment_amount is not None:
            return self.payment_amount - self.total_late_fee_payment
        return self.payment_amount

    def late_fee_payment_for(self, month: "StudentFeeMonthSelection") -> Decimal:
        if self.late_fee_payment_amount and month.fee_month == min(entry.fee_month for entry in self.months):
            return self.late_fee_payment_amount
        return month.late_fee_payment_amount


class StudentFeeWrite(StudentFeePaymentFields):
    student_id: int = Field(gt=0)
    tag: Literal["Bulk Upload"] | None = None


class StudentFeeBulkWrite(StudentFeePaymentDetails):
    receipt_number: str = Field(
        validation_alias=AliasChoices("recieptNo", "receipt_number"), min_length=1, max_length=125,
    )
    fee_month: str | int | None = Field(default=None, description="Optional Excel date fallback. Allocation uses the session balance.")
    payment_date: datetime | None = Field(default=None, description="Excel payment date or datetime.")
    payment_time: time | None = Field(default=None, description="Optional Excel time. Date-only imports use midnight.")
    payment_amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    admission_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    tag: Literal["Bulk Upload"] = "Bulk Upload"

    @field_validator("admission_number", mode="before", json_schema_input_type=str | int)
    @classmethod
    def accept_numeric_admission_number(cls, value):
        return str(value) if type(value) is int else value

    @field_validator("receipt_number")
    @classmethod
    def require_receipt_value(cls, value):
        if not value.strip():
            raise ValueError("Enter the receipt number")
        return value

    @field_validator("payment_date", mode="before", json_schema_input_type=str | int | float | None)
    @classmethod
    def read_payment_date(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return parse_imported_payment_date(value)

    @model_validator(mode="after")
    def set_import_timestamp(self):
        payment_date = self.payment_date
        if payment_date is None and self.fee_month is not None:
            try:
                if isinstance(self.fee_month, str) or self.fee_month >= 1000:
                    payment_date = parse_imported_payment_date(self.fee_month)
            except ValueError:
                pass
        if payment_date is None:
            if self.payment_time is not None:
                raise ValueError("payment_time requires a payment date")
            return self
        if self.payment_time is not None:
            payment_date = datetime.combine(
                payment_date.date(), self.payment_time, tzinfo=self.payment_time.tzinfo or payment_date.tzinfo,
            )
        if payment_date.tzinfo is None:
            payment_date = payment_date.replace(tzinfo=payment_timezone())
        self.payment_date = payment_date.astimezone(timezone.utc)
        return self


class StudentFeeMonthSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fee_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    late_fee_payment_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=16, decimal_places=2)


class StudentFeeMonthPayment(StudentFeeMonthSelection):
    payment_amount: Decimal = Field(
        ge=0, max_digits=12, decimal_places=2,
        validation_alias=AliasChoices("payment_amount", "payable_amount"),
        description="Amount collected for this month after discounts, including any carry-forward and penalties being paid.",
    )


class StudentFeeChequeSettlement(StudentFeeWrite):
    months: list[StudentFeeMonthPayment]


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


    @computed_field
    @property
    def payable_amount(self) -> Decimal:
        return self.net_amount

    @computed_field
    @property
    def status(self) -> StudentFeeStatus:
        return calculate_student_fee_status(self.net_amount, self.paid_amount)

class StudentFeeTableRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    school_id: int
    academic_session_id: int
    student_id: int
    fee_month: str
    total_amount: Decimal = Field(description="Gross monthly charge before discounts, including penalties, carry-forward and recorded late fees.")
    discount_amount: Decimal
    fine_amount: Decimal
    net_amount: Decimal
    paid_amount: Decimal
    late_fee_amount: Decimal
    status: StudentFeeStatus
    tag: str | None = None
    receipt_number: str | None = None
    payment_date: PaymentTimestamp | None = None
    payment_mode: PaymentMode | None
    description: str | None
    collected_by: str | None


class StudentFeeResponse(StudentFeeTableRecord):
    fee_head_paid_amount: Decimal
    total_accepted_payment: Decimal
    fee_heads: list[StudentFeeHeadResponse]

    @computed_field
    @property
    def due_amount(self) -> Decimal:
        return self.net_amount - self.fee_head_paid_amount


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
    receipt_number: str | None = None
    payment_date: PaymentTimestamp | None = None
    balances: list["FeePaymentMonthStatus"]
    fee_head_paid_amount: Decimal
    carry_forward_paid_amount: Decimal
    penalty_paid_amount: Decimal
    late_fee_paid_amount: Decimal


    @computed_field
    @property
    def collected_amount(self) -> Decimal:
        return self.fee_head_paid_amount + self.carry_forward_paid_amount + self.penalty_paid_amount + self.late_fee_paid_amount

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
    tag: str | None = None
    receipt_number: str | None = None
    payment_date: PaymentTimestamp | None = None
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


class StudentFeeReceiptDetails(BaseModel):
    status: StudentFeeStatus | None = None
    description: str | None = None
    cheque_number: str | None = None
    fee_head_paid_amount: Decimal | None = None
    carry_forward_paid_amount: Decimal | None = None
    penalty_paid_amount: Decimal | None = None
    late_fee_paid_amount: Decimal | None = None
    payment_data: list[StudentFeeTableRecord] = Field(default_factory=list)


class StudentFeeReceiptListResult(BaseModel):
    status: str = "success"
    total: int
    offset: int
    limit: int
    data: list[StudentFeeTableRecord]


class StudentFeeBulkRowResult(BaseModel):
    row: int
    admission_number: str
    student_id: int
    result: StudentFeePaymentResult | StudentFeeChequeResult


class StudentFeeBulkResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    data: list[StudentFeeBulkRowResult]


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
    discount_amount: Decimal = Decimal("0.00")
    penalty_amount: Decimal = Decimal("0.00")
    penalty_paid_amount: Decimal = Decimal("0.00")

    @property
    def principal_due(self) -> Decimal:
        return self.due_amount - (self.penalty_amount - self.penalty_paid_amount)


class FeePaymentFeeHeadStatus(FeePaymentHeadStatus):
    original_amount: Decimal = Field(description="Original fee-head charge before discounts and penalties.")

    @computed_field
    @property
    def status(self) -> StudentFeeStatus:
        return calculate_student_fee_status(self.payable_amount, self.paid_amount)


class FeePaymentAdjustmentStatus(FeePaymentHeadStatus):
    due_amount: Decimal = Field(description="Unapplied discount amount or unpaid penalty amount.")
    amount: Decimal = Field(description="Assigned discount or penalty amount before payment or fee-head limits.")
    status: FeeAdjustmentStatus
    effective_discount_amount: Decimal = Decimal("0.00")
    fee_head_payable_amount: Decimal | None = None
    fee_head_paid_amount: Decimal | None = None
    fee_head_due_amount: Decimal | None = None
    fee_head_status: StudentFeeStatus | None = None
    date: date
    discount_type: FeeDiscountType | None


class FeePaymentMonthStatus(BaseModel):
    fee_month: str
    payable_amount: Decimal
    paid_amount: Decimal = Field(description="Total cash recorded, including penalties, carry-forward and late fees.")
    late_fee_paid_amount: Decimal = Decimal("0.00")
    due_amount: Decimal
    status: StudentFeeStatus
    fee_adjustment: list[FeePaymentAdjustmentStatus]
    carry_forward: FeePaymentHeadStatus | None
    fee_heads: list[FeePaymentFeeHeadStatus]

    @computed_field
    @property
    def total_due_amount(self) -> Decimal:
        return self.due_amount


    @computed_field
    @property
    def collected_amount(self) -> Decimal:
        return self.paid_amount

    @computed_field
    @property
    def total_accepted_payment(self) -> Decimal:
        return self.collected_amount

    @computed_field
    @property
    def total_discount_amount(self) -> Decimal:
        return sum((line.effective_discount_amount for line in self.fee_adjustment), Decimal("0.00"))

    @computed_field
    @property
    def total_penalty_amount(self) -> Decimal:
        return sum((line.payable_amount for line in self.fee_adjustment if line.tag == "penalty"), Decimal("0.00"))

    @computed_field
    @property
    def penalty_paid_amount(self) -> Decimal:
        return sum((line.paid_amount for line in self.fee_adjustment if line.tag == "penalty"), Decimal("0.00"))

    @computed_field
    @property
    def penalty_due_amount(self) -> Decimal:
        return self.total_penalty_amount - self.penalty_paid_amount

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

    @computed_field
    @property
    def total_accepted_payment(self) -> Decimal:
        return sum((month.total_accepted_payment for month in self.data), Decimal("0.00"))

    @computed_field
    @property
    def total_payable_amount(self) -> Decimal:
        return sum((month.payable_amount for month in self.data), Decimal("0.00"))

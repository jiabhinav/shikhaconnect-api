import re
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from schemas.enums import FeeAdjustmentFrequency, FeeAdjustmentType, FeeDiscountType


class StudentFeeAdjustmentKind(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FeeAdjustmentType
    frequency: FeeAdjustmentFrequency
    discount_type: FeeDiscountType | None = None

    @model_validator(mode="after")
    def validate_discount_type(self):
        if self.type == FeeAdjustmentType.DISCOUNT and self.discount_type is None:
            raise ValueError("discount_type is required for discounts")
        if self.type == FeeAdjustmentType.PENALTY and self.discount_type is not None:
            raise ValueError("discount_type is only allowed for discounts")
        return self


class StudentFeeAdjustmentDateAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    date: Date = Field(description="Adjustment date in YYYY-MM-DD, or YYYY-MM for the first day of a month.")
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""

    @field_validator("date", mode="before")
    @classmethod
    def accept_month(cls, value):
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
            return value + "-01"
        return value

    @field_validator("description", mode="before", json_schema_input_type=str | None)
    @classmethod
    def accept_null_description(cls, value):
        return "" if value is None else value


class StudentFeeAdjustmentWrite(StudentFeeAdjustmentKind, StudentFeeAdjustmentDateAmount):
    pass


class StudentFeeAdjustmentMonthBulkWrite(StudentFeeAdjustmentKind):
    admission_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    month: list[StudentFeeAdjustmentDateAmount] = Field(min_length=1, max_length=12)


class StudentFeeAdjustmentResponse(StudentFeeAdjustmentWrite):
    model_config = ConfigDict(from_attributes=True)

    id: int
    school_id: int
    session_id: int
    student_id: int
    admission_number: str | None = None
    tag: str | None = None
    applied_at: datetime | None
    editable: bool


class StudentFeeAdjustmentResult(BaseModel):
    status: str = "success"
    message: str
    data: StudentFeeAdjustmentResponse


class SkippedDiscountMonth(BaseModel):
    fee_month: str
    due_amount: Decimal
    discount_amount: Decimal
    message: str


class StudentFeeAdjustmentCreateResult(StudentFeeAdjustmentResult):
    skipped_months: list[SkippedDiscountMonth]


class StudentFeeAdjustmentBulkRowResult(BaseModel):
    row: int
    data: list[StudentFeeAdjustmentResponse]
    skipped_months: list[SkippedDiscountMonth]


class StudentFeeAdjustmentBulkResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    data: list[StudentFeeAdjustmentBulkRowResult]


class StudentFeeAdjustmentMonthBulkRowResult(StudentFeeAdjustmentBulkRowResult):
    admission_number: str


class StudentFeeAdjustmentMonthBulkResult(StudentFeeAdjustmentBulkResult):
    data: list[StudentFeeAdjustmentMonthBulkRowResult]


class StudentFeeAdjustmentListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    offset: int
    limit: int
    data: list[StudentFeeAdjustmentResponse]


class FeeAdjustmentTotals(BaseModel):
    count: int
    total_amount: Decimal


class FeeAdjustmentStudentRow(BaseModel):
    student_id: int
    student_name: str
    admission_number: str | None
    class_id: int
    class_name: str | None
    section_id: int | None
    section_name: str | None
    status: str
    total_discount_amount: Decimal
    total_penalty_amount: Decimal
    discount: FeeAdjustmentTotals
    penalty: FeeAdjustmentTotals


class FeeAdjustmentStudentListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    offset: int
    limit: int
    data: list[FeeAdjustmentStudentRow]


class FeeAdjustmentDropdowns(BaseModel):
    types: list[FeeAdjustmentType]
    frequencies: list[FeeAdjustmentFrequency]
    discount_types: list[FeeDiscountType]


class FeeAdjustmentDropdownResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeAdjustmentDropdowns

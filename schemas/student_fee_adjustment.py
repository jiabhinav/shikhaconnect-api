from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from schemas.enums import FeeAdjustmentFrequency, FeeAdjustmentType, FeeDiscountType


class StudentFeeAdjustmentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FeeAdjustmentType
    frequency: FeeAdjustmentFrequency
    discount_type: FeeDiscountType | None = None
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    date: Date
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def validate_discount_type(self):
        if self.type == FeeAdjustmentType.DISCOUNT and self.discount_type is None:
            raise ValueError("discount_type is required for discounts")
        if self.type == FeeAdjustmentType.PENALTY and self.discount_type is not None:
            raise ValueError("discount_type is only allowed for discounts")
        return self


class StudentFeeAdjustmentResponse(StudentFeeAdjustmentWrite):
    model_config = ConfigDict(from_attributes=True)

    id: int
    school_id: int
    session_id: int
    student_id: int
    applied_at: datetime | None


class StudentFeeAdjustmentResult(BaseModel):
    status: str = "success"
    message: str
    data: StudentFeeAdjustmentResponse


class StudentFeeAdjustmentListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    data: list[StudentFeeAdjustmentResponse]


class FeeAdjustmentDropdowns(BaseModel):
    types: list[FeeAdjustmentType]
    frequencies: list[FeeAdjustmentFrequency]
    discount_types: list[FeeDiscountType]


class FeeAdjustmentDropdownResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeAdjustmentDropdowns

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from schemas.enums import StudentFeeStatus


class CarryForwardUploadRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admission_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class CarryForwardBulkWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[CarryForwardUploadRow] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_unique_admissions(self):
        if len({row.admission_number for row in self.rows}) != len(self.rows):
            raise ValueError("admission_number values must be unique within the upload")
        return self


class CarryForwardUploadResultRow(CarryForwardUploadRow):
    id: int
    student_id: int
    paid_amount: Decimal
    unpaid_amount: Decimal


class CarryForwardBulkResult(BaseModel):
    status: str = "success"
    message: str
    school_id: int
    session_id: int
    month: str
    total: int
    data: list[CarryForwardUploadResultRow]


class CarryForwardListRow(BaseModel):
    serial_number: int
    id: int | None
    student_id: int
    student_name: str
    admission_number: str | None
    class_id: int
    class_name: str | None
    section_id: int | None
    section_name: str | None
    amount: Decimal
    paid_amount: Decimal
    due_amount: Decimal
    status: StudentFeeStatus


class CarryForwardListResult(BaseModel):
    status: str = "success"
    message: str
    total: int
    total_records: int
    data: list[CarryForwardListRow]

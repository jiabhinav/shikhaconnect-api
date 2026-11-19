from decimal import Decimal
from typing import Annotated

from pydantic import (
    AfterValidator, AliasChoices, BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator,
)

from schemas.enums import StudentFeeStatus


class CarryForwardUploadRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admission_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)] = Field(
        validation_alias=AliasChoices("admission_number", "admission_id")
    )
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)

    @field_validator("admission_number", mode="before", json_schema_input_type=str | int)
    @classmethod
    def normalize_admission_number(cls, value):
        return str(value) if type(value) is int else value


def validate_unique_admissions(rows):
    if len({row.admission_number for row in rows}) != len(rows):
        raise ValueError("admission_number values must be unique within the upload")
    return rows


CarryForwardUploadRows = Annotated[
    list[CarryForwardUploadRow], Field(min_length=1, max_length=1000), AfterValidator(validate_unique_admissions)
]


class CarryForwardBulkWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: CarryForwardUploadRows


class CarryForwardRawBulkWrite(RootModel[CarryForwardUploadRows]):
    pass


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
    id: int
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

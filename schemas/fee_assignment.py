from typing import Annotated
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class FeeAssignmentWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'examples': [
        {'class_id': 1, 'section_id': 1, 'fee_structure_id': 1}
    ]})
    class_id: int = Field(gt=0)
    section_id: int = Field(gt=0)
    fee_structure_id: int = Field(gt=0)


class FeeAssignmentBatchWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'examples': [
        {'class_id': 1, 'section_id': [1, 2], 'fee_structure_id': 1}
    ]})
    class_id: int = Field(gt=0)
    section_id: list[Annotated[int, Field(gt=0)]] = Field(min_length=1)
    fee_structure_id: int = Field(gt=0)


class FeeAssignmentResponse(FeeAssignmentWrite):
    model_config = ConfigDict(from_attributes=True)
    section_id: int | None = None
    id: int
    school_id: int
    session_id: int
    class_name: str
    section_name: str | None = None
    fee_structure_name: str


class FeeAssignmentResult(BaseModel):
    status: str = 'success'
    message: str
    data: FeeAssignmentResponse


class FeeAssignmentListResult(BaseModel):
    status: str = 'success'
    message: str
    total: int
    data: list[FeeAssignmentResponse]


class AssignedFeeHeadDetail(BaseModel):
    fee_head_id: int
    fee_head_name: str
    amount: Decimal
    entered_amount: Decimal
    enabled: bool


class AssignedClassSection(BaseModel):
    assignment_id: int
    class_id: int
    class_name: str
    section_id: int | None
    section_name: str | None


class AssignedFeeStructureDetail(BaseModel):
    session_name: str
    fee_structure_id: int
    fee_structure_name: str
    fee_category_id: int | None
    fee_category_name: str | None
    assignments: list[AssignedClassSection]
    months: list['AssignedFeeMonth']
    total_session_fee: Decimal


class AssignedFeeMonth(BaseModel):
    month: str
    month_name: str
    fee_heads: list[AssignedFeeHeadDetail]
    total: Decimal


class AssignedFeeStructureDetailsResult(BaseModel):
    status: str = 'success'
    message: str
    school_id: int
    session_id: int
    total_structures: int
    data: list[AssignedFeeStructureDetail]

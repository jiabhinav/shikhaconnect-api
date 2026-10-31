from pydantic import BaseModel, ConfigDict, Field


class FeeAssignmentWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'examples': [
        {'class_id': 1, 'section_id': 1, 'fee_structure_id': 1}
    ]})
    class_id: int = Field(gt=0)
    section_id: int = Field(gt=0)
    fee_structure_id: int = Field(gt=0)


class FeeAssignmentResponse(FeeAssignmentWrite):
    model_config = ConfigDict(from_attributes=True)
    section_id: int | None = None
    id: int
    school_id: int
    session_id: int
    class_name: str
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

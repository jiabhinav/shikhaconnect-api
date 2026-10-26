from pydantic import BaseModel, ConfigDict, Field


class FeeStructureWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    fee_structure_name: str = Field(min_length=1, max_length=100)


class FeeStructureResponse(FeeStructureWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int
    session_id: int


class FeeStructureResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeStructureResponse


class FeeStructureListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[FeeStructureResponse]

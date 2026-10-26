from pydantic import BaseModel, ConfigDict, Field


class FeeHeadWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    fee_head_name: str = Field(min_length=1, max_length=100)
    student_type: str = Field(min_length=1, max_length=100)


class FeeHeadResponse(FeeHeadWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int
    session_id: int


class FeeHeadResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeHeadResponse


class FeeHeadListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[FeeHeadResponse]

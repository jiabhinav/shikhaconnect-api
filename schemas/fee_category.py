from pydantic import BaseModel, ConfigDict, Field


class FeeCategoryWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class FeeCategoryResponse(FeeCategoryWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int


class FeeCategoryResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeCategoryResponse


class FeeCategoryListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[FeeCategoryResponse]

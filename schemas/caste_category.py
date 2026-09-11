from pydantic import BaseModel, ConfigDict, Field


class CasteCategoryWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class CasteCategoryResponse(CasteCategoryWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int


class CasteCategoryResult(BaseModel):
    status: str = "success"
    message: str
    data: CasteCategoryResponse


class CasteCategoryListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[CasteCategoryResponse]

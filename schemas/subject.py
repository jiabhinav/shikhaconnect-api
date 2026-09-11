from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SubjectStatus = Literal["Active", "Inactive"]


class SubjectWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=50)
    status: SubjectStatus = "Active"

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class SubjectStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SubjectStatus


class SubjectResponse(SubjectWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int


class SubjectResult(BaseModel):
    status: str = "success"
    message: str
    data: SubjectResponse


class SubjectListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[SubjectResponse]

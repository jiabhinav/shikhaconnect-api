from pydantic import BaseModel, ConfigDict, Field


class SectionWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class ClassWrite(SectionWrite):
    # Omitted on create: append to the list. Omitted on update: keep order.
    class_order: int | None = Field(default=None, gt=0)


class SectionResponse(SectionWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int


class ClassResponse(SectionResponse):
    class_order: int


class ClassResult(BaseModel):
    status: str = "success"
    message: str
    data: ClassResponse


class ClassListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[ClassResponse]


class SectionResult(BaseModel):
    status: str = "success"
    message: str
    data: SectionResponse


class SectionListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[SectionResponse]

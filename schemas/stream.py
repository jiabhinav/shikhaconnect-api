from pydantic import BaseModel, ConfigDict, Field


class StreamWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class StreamResponse(StreamWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int


class StreamResult(BaseModel):
    status: str = "success"
    message: str
    data: StreamResponse


class StreamListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[StreamResponse]

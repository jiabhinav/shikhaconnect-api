from pydantic import BaseModel, ConfigDict, Field


class HouseWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class HouseResponse(HouseWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int


class HouseResult(BaseModel):
    status: str = "success"
    message: str
    data: HouseResponse


class HouseListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[HouseResponse]

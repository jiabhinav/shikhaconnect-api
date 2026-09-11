from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class SessionResponse(SessionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    school_id: int
    status: Literal["Past", "Current", "Upcoming"]


class SessionResult(BaseModel):
    status: str = "success"
    message: str
    data: SessionResponse


class SessionListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[SessionResponse]

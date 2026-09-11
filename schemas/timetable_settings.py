from datetime import time
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TimetableSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summer_start_time: time
    summer_end_time: time
    winter_start_time: time
    winter_end_time: time
    minimum_attendance_percentage: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    term_attendance_enabled: bool = Field(strict=True)

    @field_validator("summer_start_time", "summer_end_time", "winter_start_time", "winter_end_time", mode="before")
    @classmethod
    def require_clock_time(cls, value):
        if not isinstance(value, (str, time)):
            raise ValueError("Use a local clock time such as 08:00:00")
        return value

    @model_validator(mode="after")
    def validate_time_ranges(self):
        for season in ("summer", "winter"):
            start = getattr(self, f"{season}_start_time")
            end = getattr(self, f"{season}_end_time")
            if start.tzinfo is not None or end.tzinfo is not None:
                raise ValueError("Use local school times without timezone offsets")
            if end <= start:
                raise ValueError(f"{season}_end_time must be after {season}_start_time")
        return self


class TimetableSettingsResponse(TimetableSettingsWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int


class TimetableSettingsResult(BaseModel):
    status: str = "success"
    message: str
    data: TimetableSettingsResponse

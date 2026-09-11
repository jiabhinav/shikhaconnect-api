from pydantic import BaseModel, ConfigDict, Field


class GenerationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_day: int = Field(ge=1, le=31, strict=True)
    late_fee_enabled: bool = Field(default=False, strict=True)


class FeeSettingsWrite(GenerationBase):
    payment_due_days: int = Field(ge=0, le=2147483647, strict=True)


class TransportSettingsWrite(GenerationBase):
    payment_due_day: int = Field(ge=1, le=31, strict=True)


class FeeSettingsResponse(FeeSettingsWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int


class TransportSettingsResponse(TransportSettingsWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int


class FeeSettingsResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeSettingsResponse


class TransportSettingsResult(BaseModel):
    status: str = "success"
    message: str
    data: TransportSettingsResponse

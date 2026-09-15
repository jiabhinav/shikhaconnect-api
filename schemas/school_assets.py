from enum import Enum

from pydantic import BaseModel, ConfigDict


class SchoolAssetField(str, Enum):
    SCHOOL_LOGO = "school_logo"
    BOARD_LOGO = "board_logo"
    PRINCIPAL_SIGNATURE = "principal_signature"
    EXAM_COORDINATOR_SIGNATURE = "exam_coordinator_signature"


class SchoolAssetsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    school_id: int
    school_logo: str | None = None
    board_logo: str | None = None
    principal_signature: str | None = None
    exam_coordinator_signature: str | None = None


class SchoolAssetsResult(BaseModel):
    status: str = "success"
    message: str
    data: SchoolAssetsResponse

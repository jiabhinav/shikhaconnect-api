from pydantic import BaseModel, ConfigDict, Field

from models.school_mapping import SchoolMappingStatus
from models.user import UserRole, UserStatus


class SchoolMappingStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SchoolMappingStatus


class SchoolMappingWrite(SchoolMappingStatusUpdate):
    school_id: int = Field(gt=0)
    user_id: int = Field(gt=0, description="Login user ID (login_user.id)")


class SchoolMappingResponse(SchoolMappingWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int


class SchoolMappingResult(BaseModel):
    status: str = "success"
    message: str
    data: SchoolMappingResponse


class MappedUserResponse(BaseModel):
    id: int
    school_id: int
    user_id: int
    status: SchoolMappingStatus
    first_name: str
    middle_name: str | None
    last_name: str | None
    email: str
    mobile: str
    role: UserRole | str
    user_status: UserStatus


class SchoolUserListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[MappedUserResponse]

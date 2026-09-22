from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from models.user import UserRole, UserStatus
from models.school_mapping import SchoolMappingStatus
from schemas.school import SchoolAddress, SchoolDetails
from schemas.session import SessionResponse


class UserCreate(BaseModel):
    first_name: str
    middle_name: Optional[str] = None
    last_name: str
    email: EmailStr
    mobile: str
    password: Optional[str] = None
    date_of_birth: Optional[str] = None
    designation: Optional[str] = None
    aadhaar_number: Optional[str] = None
    nationality: Optional[str] = None
    spouse_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    description: Optional[str] = None
    gender: Optional[str] = None
    line_1: Optional[str] = None
    line_2: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    role: UserRole = UserRole.ADMIN
    status: UserStatus = UserStatus.ACTIVE


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None
    password: Optional[str] = None
    date_of_birth: Optional[str] = None
    designation: Optional[str] = None
    aadhaar_number: Optional[str] = None
    nationality: Optional[str] = None
    spouse_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    description: Optional[str] = None
    gender: Optional[str] = None
    line_1: Optional[str] = None
    line_2: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None


class UserStatusUpdate(BaseModel):
    status: bool


class UserResponse(BaseModel):
    id: int
    first_name: str
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    mobile: str
    date_of_birth: Optional[str] = None
    designation: Optional[str] = None
    aadhaar_number: Optional[str] = None
    nationality: Optional[str] = None
    spouse_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    description: Optional[str] = None
    gender: Optional[str] = None
    line_1: Optional[str] = None
    line_2: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    role: UserRole | str
    status: UserStatus

    class Config:
        from_attributes = True


class UserAssignedSchool(SchoolDetails, SchoolAddress):
    id: int
    mapping_status: SchoolMappingStatus = SchoolMappingStatus.ACTIVE

    model_config = {"from_attributes": True}


class UserDetail(UserResponse):
    schools: list[UserAssignedSchool] = Field(default_factory=list)


class UserDetailResult(BaseModel):
    status: str = "success"
    message: str
    data: UserDetail


class UserListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[UserDetail]


class UserLogin(BaseModel):
    mobile: str
    password: str


class PasswordResetRequest(BaseModel):
    mobile: str = Field(min_length=1)
    new_password: Optional[str] = Field(
        default=None, min_length=1,
        description="New password. Omit or send null to use the user's mobile number.",
    )


class PasswordResetResponse(BaseModel):
    status: str = "success"
    message: str = "Password reset successfully"


class UserRegisterResponse(BaseModel):
    status: str = "success"
    message: str = "User registered successfully"
    data: UserResponse


class LoginSchoolPermission(BaseModel):
    id: int
    school_id: int
    module_id: int
    name: Optional[str] = None
    is_enabled: bool

    model_config = {"from_attributes": True}


class LoginStaffPermission(BaseModel):
    id: int
    login_user_id: int
    staff_module_id: int
    name: Optional[str] = None
    is_enabled: bool

    model_config = {"from_attributes": True}


class LoginSchool(SchoolDetails, SchoolAddress):
    id: int
    school_logo: Optional[str] = None
    sessions: list[SessionResponse] = Field(default_factory=list)
    permissions: list[LoginSchoolPermission | LoginStaffPermission] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class UserLoginData(UserResponse):
    schools: Optional[list[LoginSchool]] = None


class UserLoginResponse(BaseModel):
    status: str = "success"
    message: str = "Login successful"
    data: UserLoginData
    token: str
    token_type: str = "bearer"

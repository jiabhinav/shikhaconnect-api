from pydantic import BaseModel, EmailStr
from typing import Optional

from models.user import UserRole, UserStatus


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
    school_name: Optional[str] = None
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
    school_name: Optional[str] = None
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None


class UserStatusUpdate(BaseModel):
    status: bool


class UserResponse(BaseModel):
    id: int
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
    school_name: Optional[str] = None
    role: UserRole
    status: UserStatus

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    mobile: str
    password: str


class UserRegisterResponse(BaseModel):
    status: str = "success"
    message: str = "User registered successfully"
    data: UserResponse


class UserLoginResponse(BaseModel):
    status: str = "success"
    message: str = "Login successful"
    data: UserResponse
    token: str
    token_type: str = "bearer"

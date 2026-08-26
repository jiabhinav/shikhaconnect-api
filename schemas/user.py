from pydantic import BaseModel, EmailStr

from models.user import UserRole, UserStatus


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    mobile: str
    school_name: str
    role: UserRole = UserRole.ADMIN
    status: UserStatus = UserStatus.ACTIVE


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    mobile: str
    school_name: str
    role: UserRole
    status: UserStatus

    class Config:
        from_attributes = True


class UserRegisterResponse(BaseModel):
    status: str = "success"
    message: str = "User registered successfully"
    data: UserResponse
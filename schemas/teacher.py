from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from schemas.staff import StaffInfo, StaffInfoResponse, StaffStatusUpdate
from schemas.student import StudentAddress


class TeacherInfo(StaffInfo):
    role: Literal["Teacher"] = "Teacher"
    mother_name: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)
    aadhaar_number: str | None = Field(default=None, max_length=20)
    caste_category_id: int = Field(gt=0)
    alternate_contact_no: str | None = Field(default=None, max_length=20)


class TeacherAddressInfo(StudentAddress):
    city: str | None = Field(default=None, max_length=255)


class TeacherWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_info: TeacherInfo
    address: TeacherAddressInfo


class TeacherInfoResponse(StaffInfoResponse):
    employee_code: str | None = None
    email: str | None = None
    alternate_contact_no: str | None = None


class TeacherResponse(BaseModel):
    id: int
    school_id: int
    teacher_login_id: int
    teacher_info: TeacherInfoResponse
    address: TeacherAddressInfo

    @model_validator(mode="before")
    @classmethod
    def from_teacher(cls, value):
        if not isinstance(value, dict):
            return {"id": value.id, "school_id": value.school_id, "teacher_login_id": value.teacher_login_id,
                    "teacher_info": TeacherInfoResponse.model_validate(value),
                    "address": value.address}
        return value


class TeacherResult(BaseModel):
    status: str = "success"
    message: str
    data: TeacherResponse


class TeacherListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[TeacherResponse]


class TeacherStatusUpdate(StaffStatusUpdate):
    pass

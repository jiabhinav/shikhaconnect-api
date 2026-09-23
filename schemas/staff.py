from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from schemas.student import FormSection
from models.staff import StaffRole


class StaffInfo(FormSection):
    first_name: str = Field(min_length=1, max_length=255)
    middle_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    date_of_birth: date
    designation: str = Field(min_length=1, max_length=255)
    mobile_number: str = Field(min_length=1, max_length=20)
    email: EmailStr = Field(max_length=255)
    spouse_name: str | None = Field(default=None, max_length=255)
    father_name: str = Field(min_length=1, max_length=255)
    mother_name: str = Field(min_length=1, max_length=255)
    nationality: str = Field(min_length=1, max_length=100)
    aadhaar_number: str = Field(min_length=1, max_length=20)
    religion: str | None = Field(default=None, max_length=100)
    caste_category_id: int = Field(gt=0)
    qualification: str | None = Field(default=None, max_length=255)
    joining_date: date | None = None
    biometric_code: str | None = Field(default=None, max_length=100)
    experience: str | None = Field(default=None, max_length=255)
    mode_of_transport: str | None = Field(default=None, max_length=100)
    salary: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    blood_group: str | None = Field(default=None, max_length=10)
    role: StaffRole
    gender: str = Field(min_length=1, max_length=50)
    feedback: str | None = None


class StaffInfoWrite(StaffInfo):
    password: str | None = Field(default=None, min_length=1)


class StaffAddressInfo(FormSection):
    line_1: str = Field(min_length=1, max_length=500)
    line_2: str | None = Field(default=None, max_length=500)
    city: str = Field(min_length=1, max_length=255)
    country: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=255)
    pin_code: str = Field(min_length=1, max_length=20)
    id: int | None = None
    login_user_id: int | None = None


class StaffPermissionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    staff_module_id: int = Field(gt=0)
    is_enabled: bool = True


class StaffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staff_info: StaffInfoWrite
    address: StaffAddressInfo
    permissions: list[StaffPermissionWrite] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_permissions(self):
        ids = [permission.staff_module_id for permission in self.permissions]
        if len(ids) != len(set(ids)):
            raise ValueError("Permission module IDs cannot contain duplicates")
        return self


class StaffPermissionResponse(StaffPermissionWrite):
    id: int
    login_user_id: int
    name: str | None = None


class StaffInfoResponse(BaseModel):
    """Stored profiles may be incomplete; write validation stays in StaffInfo."""
    model_config = ConfigDict(from_attributes=True)

    first_name: str
    middle_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    designation: str | None = None
    mobile_number: str
    email: str
    spouse_name: str | None = None
    father_name: str | None = None
    mother_name: str | None = None
    nationality: str | None = None
    aadhaar_number: str | None = None
    religion: str | None = None
    caste_category_id: int | None = None
    qualification: str | None = None
    joining_date: date | None = None
    biometric_code: str | None = None
    experience: str | None = None
    mode_of_transport: str | None = None
    salary: Decimal | None = None
    blood_group: str | None = None
    role: str
    gender: str | None = None
    feedback: str | None = None


class StaffAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login_user_id: int
    line_1: str | None = None
    line_2: str | None = None
    city: str | None = None
    country: str | None = None
    state: str | None = None
    pin_code: str | None = None


class StaffResponse(BaseModel):
    id: int
    login_user_id: int
    school_id: int
    staff_info: StaffInfoResponse
    address: StaffAddressResponse | None
    permissions: list[StaffPermissionResponse]

    @model_validator(mode="before")
    @classmethod
    def from_staff(cls, value):
        if not isinstance(value, dict):
            return {"id": value.id, "school_id": value.school_id,
                    "login_user_id": value.login_user_id,
                    "staff_info": StaffInfoResponse.model_validate(value),
                    "address": value.address, "permissions": value.permissions}
        return value


class StaffResult(BaseModel):
    status: str = "success"
    message: str
    data: StaffResponse


class StaffListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[StaffResponse]

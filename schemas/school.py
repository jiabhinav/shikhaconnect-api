from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class SchoolInfo(BaseModel):
    school_name: str = Field(min_length=1, max_length=255)
    branch_name: str = Field(min_length=1, max_length=255)
    school_code: Optional[str] = None
    school_affiliation_no: Optional[str] = None
    school_website: Optional[str] = None
    primary_email: EmailStr
    secondary_email: Optional[EmailStr] = None
    primary_number: str = Field(min_length=5, max_length=20)
    secondary_number: Optional[str] = None
    board_name: Optional[str] = None
    board_tag_line_1: Optional[str] = None
    board_tag_line_2: Optional[str] = None
    admission_prefix: Optional[str] = None
    admission_suffix: Optional[str] = None
    start_admission_no: int = Field(ge=0)
    employee_prefix: Optional[str] = None
    employee_suffix: Optional[str] = None
    start_employee_no: int = Field(ge=0)
    sender_id: Optional[str] = None
    u_dais_code: Optional[str] = None
    season: str = Field(min_length=1)
    session_name: str = Field(min_length=1)
    session_start_date: date
    session_end_date: date
    description: Optional[str] = None
    principal_name: str = Field(min_length=1)
    exam_coordinator_name: Optional[str] = None

    @model_validator(mode="after")
    def validate_session_dates(self):
        if self.session_end_date < self.session_start_date:
            raise ValueError("session_end_date must be on or after session_start_date")
        return self


class SchoolAddress(BaseModel):
    line_1: Optional[str] = None
    line_2: Optional[str] = None
    city: str = Field(min_length=1)
    district: Optional[str] = None
    state: str = Field(min_length=1)
    country: str = Field(min_length=1)
    pin_code: Optional[str] = None
    landmark: Optional[str] = None
    landline: Optional[str] = None


class FeePayment(BaseModel):
    account_no: Optional[str] = None
    account_name: Optional[str] = None
    ifsc: Optional[str] = None
    bank_name: Optional[str] = None
    merchant_id: Optional[str] = None
    merchant_key: Optional[str] = None
    merchant_salt: Optional[str] = None


class TransportPayment(BaseModel):
    transport_id: Optional[str] = None
    transport_name: Optional[str] = None
    transport_mode: Optional[str] = None
    transport_rate: Optional[Decimal] = Field(default=None, ge=0)
    vehicle_type: Optional[str] = None
    transport_status: Optional[str] = None


class AssignAdmin(BaseModel):
    admin_id: int = Field(gt=0)


class AssignSubAdmins(BaseModel):
    sub_admin_ids: list[int] = Field(default_factory=list)

    @field_validator("sub_admin_ids")
    @classmethod
    def unique_sub_admins(cls, value: list[int]) -> list[int]:
        if any(user_id <= 0 for user_id in value):
            raise ValueError("sub_admin_ids must contain positive user IDs")
        if len(value) != len(set(value)):
            raise ValueError("sub_admin_ids cannot contain duplicates")
        return value


class AddServices(BaseModel):
    services: list[str] = Field(default_factory=list)

    @field_validator("services")
    @classmethod
    def clean_services(cls, value: list[str]) -> list[str]:
        cleaned = [service.strip() for service in value if service.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("services cannot contain duplicates")
        return cleaned


class SchoolCreate(BaseModel):
    school_info: SchoolInfo
    address: SchoolAddress
    fee_payment: FeePayment = Field(default_factory=FeePayment)
    transport_payment: TransportPayment = Field(default_factory=TransportPayment)
    assign_admin: AssignAdmin
    assign_sub_admin: AssignSubAdmins = Field(default_factory=AssignSubAdmins)
    add_services: AddServices = Field(default_factory=AddServices)

    @model_validator(mode="after")
    def validate_assignments(self):
        if self.assign_admin.admin_id in self.assign_sub_admin.sub_admin_ids:
            raise ValueError("The admin cannot also be assigned as a sub admin")
        return self

    def school_values(self) -> dict:
        values = {}
        for section in (self.school_info, self.address, self.fee_payment, self.transport_payment):
            values.update(section.model_dump())
        values["primary_email"] = str(self.school_info.primary_email)
        values["secondary_email"] = str(self.school_info.secondary_email) if self.school_info.secondary_email else None
        return values

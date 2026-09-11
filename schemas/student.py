from datetime import date
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class FormSection(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", from_attributes=True)

    @field_validator("*", mode="before")
    @classmethod
    def empty_optional_is_none(cls, value, info):
        if not cls.model_fields[info.field_name].is_required() and isinstance(value, str):
            return value.strip() or None
        return value


class StudentInfo(FormSection):
    date_of_birth: date
    caste_category_id: int = Field(gt=0)
    fee_category_id: int = Field(gt=0)
    session_id: int = Field(gt=0)
    class_id: int = Field(gt=0)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    mobile_number: str = Field(min_length=1, max_length=20)
    gender: str = Field(min_length=1, max_length=50)
    email: EmailStr = Field(min_length=1, max_length=255)
    nationality: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=255)
    blood_group: str | None = Field(default=None, max_length=10)
    religion: str | None = Field(default=None, max_length=100)
    aadhaar_number: str | None = Field(default=None, max_length=20)
    permanent_education_no: str | None = Field(default=None, max_length=100)


class ParentInfo(FormSection):
    father_name: str = Field(min_length=1, max_length=255)
    father_contact_no: str = Field(min_length=1, max_length=20)
    father_aadhaar_no: str = Field(min_length=1, max_length=20)
    mother_name: str = Field(min_length=1, max_length=255)
    father_secondary_number: str | None = Field(default=None, max_length=20)
    father_qualification: str | None = Field(default=None, max_length=255)
    father_occupation: str | None = Field(default=None, max_length=255)
    father_office_address: str | None = Field(default=None, max_length=1000)
    mother_contact_no: str | None = Field(default=None, max_length=20)
    mother_secondary_number: str | None = Field(default=None, max_length=20)
    mother_qualification: str | None = Field(default=None, max_length=255)
    mother_occupation: str | None = Field(default=None, max_length=255)
    mother_aadhaar_no: str | None = Field(default=None, max_length=20)
    mother_office_address: str | None = Field(default=None, max_length=1000)
    guardian_name: str | None = Field(default=None, max_length=255)
    relation_with_student: str | None = Field(default=None, max_length=100)
    guardian_primary_contact_no: str | None = Field(default=None, max_length=20)
    guardian_secondary_contact_no: str | None = Field(default=None, max_length=20)
    guardian_email: EmailStr | None = Field(default=None, max_length=255)
    guardian_address: str | None = Field(default=None, max_length=1000)


class StudentAddress(FormSection):
    line_1: str = Field(min_length=1, max_length=500)
    city: str = Field(min_length=1, max_length=255)
    country: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=255)
    pin_code: str = Field(min_length=1, max_length=20)
    line_2: str | None = Field(default=None, max_length=500)
    address_type: str | None = Field(default=None, max_length=100)


class StudentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_info: StudentInfo
    parent_info: ParentInfo
    address: StudentAddress

    def student_values(self):
        return {key: value for section in (self.student_info, self.parent_info, self.address)
                for key, value in section.model_dump().items()}


class StudentResponse(StudentWrite):
    id: int
    school_id: int

    @model_validator(mode="before")
    @classmethod
    def from_student(cls, value):
        if not isinstance(value, dict):
            return {"id": value.id, "school_id": value.school_id,
                    "student_info": StudentInfo.model_validate(value),
                    "parent_info": ParentInfo.model_validate(value),
                    "address": StudentAddress.model_validate(value)}
        return value


class StudentResult(BaseModel):
    status: str = "success"
    message: str
    data: StudentResponse


class StudentListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[StudentResponse]

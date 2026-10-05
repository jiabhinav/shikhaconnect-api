from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, field_validator, model_validator


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
    section_id: int = Field(gt=0)
    roll_number: str | None = Field(default=None, max_length=50)
    apar_id: str | None = Field(default=None, max_length=100)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    mobile_number: str = Field(min_length=1, max_length=20)
    gender: str = Field(min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=255)
    nationality: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=255)
    blood_group: str | None = Field(default=None, max_length=10)
    religion: str | None = Field(default=None, max_length=100)
    aadhaar_number: str | None = Field(default=None, max_length=20)
    house_id: int | None = Field(default=None, gt=0)
    student_type: str | None = Field(default=None, max_length=100)
    admission_type: str | None = Field(default=None, max_length=100)
    first_admission_class: str | None = Field(default=None, max_length=255)
    abha_number: str | None = Field(default=None, max_length=100)
    mode_of_transport: str | None = Field(default=None, max_length=100)
    weight_kg: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    height_cm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    permanent_education_no: str | None = Field(default=None, max_length=100)


class ParentInfo(FormSection):
    father_name: str = Field(min_length=1, max_length=255)
    father_contact_no: str | None = Field(default=None, max_length=20)
    father_aadhaar_no: str | None = Field(default=None, max_length=20)
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


class PreviousSchoolInfo(FormSection):
    school_name: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    class_name: str | None = Field(default=None, max_length=255)
    session: str | None = Field(default=None, max_length=100)


class PreviousSchoolResponse(PreviousSchoolInfo):
    id: int
    student_id: int
    school_id: int


class StudentAddress(FormSection):
    landline_number: str | None = Field(default=None, max_length=20)
    district: str | None = Field(default=None, max_length=255)
    line_1: str = Field(min_length=1, max_length=500)
    city: str = Field(min_length=1, max_length=255)
    country: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=255)
    pin_code: str | None = Field(default=None, max_length=20)
    line_2: str | None = Field(default=None, max_length=500)


class StudentStatusUpdate(FormSection):
    status: StrictBool


class StudentAdmissionUpdate(FormSection):
    admission_number: str = Field(min_length=1, max_length=150)


class StudentLoginResponse(FormSection):
    id: int
    mobile: str
    school_id: int


class StudentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_info: StudentInfo
    parent_info: ParentInfo
    present_address: StudentAddress
    permanent_address: StudentAddress

    previous_school: PreviousSchoolInfo | None = None

    def student_values(self):
        address = self.present_address
        return {**self.student_info.model_dump(), **self.parent_info.model_dump(),
                **address.model_dump(exclude={"district", "landline_number"})}


class StudentInfoResponse(StudentInfo):
    status: Literal["active", "inactive"]
    admission_number: str | None = None
    admission_sequence: int | None = None
    # Older records may not have these details until they are updated.
    section_id: int | None = None
    apar_id: str | None = None


class StudentResponse(BaseModel):
    id: int
    school_id: int
    student_info: StudentInfoResponse
    parent_info: ParentInfo
    present_address: StudentAddress
    permanent_address: StudentAddress
    previous_school: PreviousSchoolResponse | None = None
    login: StudentLoginResponse | None

    @model_validator(mode="before")
    @classmethod
    def from_student(cls, value):
        if not isinstance(value, dict):
            addresses = {a.address_type: StudentAddress.model_validate(a) for a in value.addresses}
            legacy = StudentAddress.model_validate(value)
            return {"id": value.id, "school_id": value.school_id,
                    "student_info": StudentInfoResponse.model_validate(value),
                    "parent_info": ParentInfo.model_validate(value),
                    "present_address": addresses.get("present", legacy),
                    "permanent_address": addresses.get("permanent", legacy),
                    "login": value.login, "previous_school": value.previous_school}
        return value


class StudentResult(BaseModel):
    status: str = "success"
    message: str
    data: StudentResponse


class StudentListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[StudentResponse]


class StudentDropdownOption(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    session_id: int | None


class StudentClassOption(StudentDropdownOption):
    class_order: int


class StudentDropdownData(BaseModel):
    school_id: int
    session_id: int | None
    caste_categories: list[StudentDropdownOption]
    fee_categories: list[StudentDropdownOption]
    classes: list[StudentClassOption]
    sections: list[StudentDropdownOption]
    houses: list[StudentDropdownOption]


class StudentDropdownResult(BaseModel):
    status: str = "success"
    message: str
    data: StudentDropdownData

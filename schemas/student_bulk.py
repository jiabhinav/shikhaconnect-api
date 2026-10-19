from copy import deepcopy
from datetime import datetime
import re
from pydantic import AliasChoices, Field, create_model, BaseModel, ConfigDict, field_validator
from schemas.student import FormSection, StudentInfo, StudentWrite, ParentInfo, StudentAddress, PreviousSchoolInfo


class _StudentBulkRow(FormSection):
    admission_number: str = Field(min_length=1, max_length=150)
    first_name: str = Field(min_length=1, max_length=255, validation_alias=AliasChoices('name', 'first_name'))
    father_name: str = Field(min_length=1, max_length=255)
    mobile_number: str = Field(min_length=1, max_length=20)

    @field_validator('admission_date', 'date_of_birth', 'father_date_of_birth',
                     'mother_date_of_birth', mode='before', check_fields=False)
    @classmethod
    def normalize_date(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r'\d{2}([/.-])\d{2}\1\d{4}', value):
                # Day first for all supported spreadsheet text date formats.
                return datetime.strptime(value.replace('/', '-').replace('.', '-'), '%d-%m-%Y').date()
        return value


class StudentBulkInfo(StudentInfo):
    caste_category_name: str | None = Field(default=None, min_length=1, max_length=100)
    fee_category_name: str | None = Field(default=None, min_length=1, max_length=100)


class StudentBulkWrite(StudentWrite):
    student_info: StudentBulkInfo


# Derive the Excel fields from the Add Student form so validation stays aligned.
BULK_COLUMNS = {}
for section, model in (("student_info", StudentInfo), ("parent_info", ParentInfo),
                       ("present_address", StudentAddress), ("permanent_address", StudentAddress),
                       ("previous_school", PreviousSchoolInfo)):
    for name, original in model.model_fields.items():
        key = original.serialization_alias or name
        column = key if section in ("student_info", "parent_info") else f"{section}.{key}"
        BULK_COLUMNS[column] = (section, key)

extra_fields = {}
for section, model in (("student_info", StudentInfo), ("parent_info", ParentInfo),
                       ("present_address", StudentAddress), ("permanent_address", StudentAddress),
                       ("previous_school", PreviousSchoolInfo)):
    for name, original in model.model_fields.items():
        key = original.serialization_alias or name
        column = key if section in ("student_info", "parent_info") else f"{section}.{key}"
        if column in _StudentBulkRow.model_fields:
            continue
        field = deepcopy(original)
        field.default = None
        field.alias = field.validation_alias = field.serialization_alias = None
        extra_fields[column] = (original.annotation | None, field)

StudentBulkRow = create_model("StudentBulkRow", __base__=_StudentBulkRow, **extra_fields)


def bulk_payload(row, session_id, class_id, section_id, caste_category_name=None, fee_category_name=None):
    data = {section: {} for section in ("student_info", "parent_info", "present_address", "permanent_address", "previous_school")}
    for column, value in row.model_dump(exclude_none=True).items():
        if column in BULK_COLUMNS:
            section, key = BULK_COLUMNS[column]
            data[section][key] = value
    info = data["student_info"]
    for key, value in (("session_id", session_id), ("class_id", class_id), ("section_id", section_id)):
        if key in info and info[key] != value:
            raise ValueError(f"{key} in Excel must match the query parameter")
        info[key] = value
    info.setdefault("caste_category_name", caste_category_name)
    info.setdefault("fee_category_name", fee_category_name)
    info.setdefault("gender", "Not specified")
    info.setdefault("nationality", "Not specified")
    data["parent_info"].setdefault("mother_name", "Not specified")
    for section in ("present_address", "permanent_address"):
        for key in ("line_1", "city", "country", "state"):
            data[section].setdefault(key, "Not specified")
    if not data["previous_school"]:
        data["previous_school"] = None
    return StudentBulkWrite.model_validate(data)


class FailedStudentExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(gt=0)
    admission_number: str = ''
    name: str = ''
    father_name: str = ''
    mobile_number: str = ''
    reason: str
    errors: list[dict] | None = None


class FailedStudentsExportRequest(FormSection):
    failed_students: list[FailedStudentExport] = Field(min_length=1, max_length=1000)

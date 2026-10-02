from sqlalchemy import Column, Date, ForeignKey, Integer, String, UniqueConstraint, CheckConstraint
from database.database import Base
from sqlalchemy.orm import relationship


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("school_id", "admission_number", name="uq_student_admission"),
        UniqueConstraint("school_id", "admission_sequence", name="uq_student_admission_sequence"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_student_status"),
    )
    login_id = Column(Integer, ForeignKey("student_login.id", ondelete="RESTRICT"), nullable=True, index=True)
    admission_number = Column(String(150), nullable=True)
    admission_sequence = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="active", server_default="active")
    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False, index=True)
    date_of_birth = Column(Date, nullable=False)
    caste_category_id = Column(Integer, ForeignKey("caste_categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    fee_category_id = Column(Integer, ForeignKey("fee_categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False, index=True)
    section_id = Column(Integer, ForeignKey("sections.id", ondelete="RESTRICT"), nullable=True, index=True)
    roll_number = Column(String(50), nullable=True)
    first_name = Column(String(255), nullable=False)
    last_name = Column(String(255), nullable=False)
    mobile_number = Column(String(20), nullable=False)
    gender = Column(String(50), nullable=False)
    email = Column(String(255), nullable=False)
    nationality = Column(String(100), nullable=False)
    father_name = Column(String(255), nullable=False)
    father_contact_no = Column(String(20), nullable=True)
    father_aadhaar_no = Column(String(20), nullable=True)
    mother_name = Column(String(255), nullable=False)
    line_1 = Column(String(500), nullable=False)
    city = Column(String(255), nullable=False)
    country = Column(String(100), nullable=False)
    state = Column(String(255), nullable=False)
    pin_code = Column(String(20), nullable=True)
    middle_name = Column(String(255), nullable=True)
    blood_group = Column(String(10), nullable=True)
    religion = Column(String(100), nullable=True)
    aadhaar_number = Column(String(20), nullable=True)
    permanent_education_no = Column(String(100), nullable=True)
    father_secondary_number = Column(String(20), nullable=True)
    father_qualification = Column(String(255), nullable=True)
    father_occupation = Column(String(255), nullable=True)
    father_office_address = Column(String(1000), nullable=True)
    mother_contact_no = Column(String(20), nullable=True)
    mother_secondary_number = Column(String(20), nullable=True)
    mother_qualification = Column(String(255), nullable=True)
    mother_occupation = Column(String(255), nullable=True)
    mother_aadhaar_no = Column(String(20), nullable=True)
    mother_office_address = Column(String(1000), nullable=True)
    guardian_name = Column(String(255), nullable=True)
    relation_with_student = Column(String(100), nullable=True)
    guardian_primary_contact_no = Column(String(20), nullable=True)
    guardian_secondary_contact_no = Column(String(20), nullable=True)
    guardian_email = Column(String(255), nullable=True)
    guardian_address = Column(String(1000), nullable=True)
    line_2 = Column(String(500), nullable=True)
    address_type = Column(String(100), nullable=True)

    apar_id = Column(String(100), nullable=True)
    login = relationship("StudentLogin", uselist=False)
    addresses = relationship("StudentAddressRecord", cascade="all, delete-orphan")


class StudentLogin(Base):
    __tablename__ = "student_login"
    __table_args__ = (UniqueConstraint("school_id", "mobile", name="uq_student_login_school_mobile"),)
    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False, index=True)
    mobile = Column(String(20), nullable=False)
    password = Column(String(500), nullable=False)


class StudentAddressRecord(Base):
    __tablename__ = "student_addresses"
    __table_args__ = (
        UniqueConstraint("student_id", "address_type", name="uq_student_address_type"),
        CheckConstraint("address_type IN ('present', 'permanent')", name="ck_student_address_type"),
    )
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    address_type = Column(String(20), nullable=False)
    line_1 = Column(String(500), nullable=False)
    line_2 = Column(String(500), nullable=True)
    city = Column(String(255), nullable=False)
    district = Column(String(255), nullable=True)
    state = Column(String(255), nullable=False)
    country = Column(String(100), nullable=False)
    pin_code = Column(String(20), nullable=True)

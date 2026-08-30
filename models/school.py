from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from database.database import Base


class School(Base):
    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, index=True)
    school_name = Column(String(255), nullable=False)
    branch_name = Column(String(255), nullable=False)
    school_code = Column(String(100), unique=True, nullable=True, index=True)
    school_affiliation_no = Column(String(100), unique=True, nullable=True)
    school_website = Column(String(500), nullable=True)
    primary_email = Column(String(255), nullable=False, index=True)
    secondary_email = Column(String(255), nullable=True)
    primary_number = Column(String(20), nullable=False)
    secondary_number = Column(String(20), nullable=True)
    board_name = Column(String(255), nullable=True)
    board_tag_line_1 = Column(String(255), nullable=True)
    board_tag_line_2 = Column(String(255), nullable=True)
    admission_prefix = Column(String(50), nullable=True)
    admission_suffix = Column(String(50), nullable=True)
    start_admission_no = Column(Integer, nullable=False)
    employee_prefix = Column(String(50), nullable=True)
    employee_suffix = Column(String(50), nullable=True)
    start_employee_no = Column(Integer, nullable=False)
    sender_id = Column(String(100), nullable=True)
    u_dais_code = Column(String(100), unique=True, nullable=True)
    season = Column(String(100), nullable=False)
    session_name = Column(String(100), nullable=False)
    session_start_date = Column(Date, nullable=False)
    session_end_date = Column(Date, nullable=False)
    description = Column(Text, nullable=True)
    principal_name = Column(String(255), nullable=False)
    exam_coordinator_name = Column(String(255), nullable=True)

    line_1 = Column(String(255), nullable=True)
    line_2 = Column(String(255), nullable=True)
    city = Column(String(255), nullable=False)
    district = Column(String(255), nullable=True)
    state = Column(String(255), nullable=False)
    country = Column(String(255), nullable=False)
    pin_code = Column(String(20), nullable=True)
    landmark = Column(String(255), nullable=True)
    landline = Column(String(30), nullable=True)

    account_no = Column(String(100), nullable=True)
    account_name = Column(String(255), nullable=True)
    ifsc = Column(String(50), nullable=True)
    bank_name = Column(String(255), nullable=True)
    merchant_id = Column(String(255), nullable=True)
    merchant_key = Column(String(500), nullable=True)
    merchant_salt = Column(String(500), nullable=True)

    transport_id = Column(String(100), nullable=True)
    transport_name = Column(String(255), nullable=True)
    transport_mode = Column(String(100), nullable=True)
    transport_rate = Column(Numeric(12, 2), nullable=True)
    vehicle_type = Column(String(100), nullable=True)
    transport_status = Column(String(50), nullable=True)

    assignments = relationship("SchoolUserAssignment", cascade="all, delete-orphan", back_populates="school")
    permissions = relationship("SchoolPermission", cascade="all, delete-orphan", back_populates="school")


class SchoolUserAssignment(Base):
    __tablename__ = "school_user_assignments"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_school_user_assignment"),)

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    role = Column(String(50), nullable=False)

    school = relationship("School", back_populates="assignments")
    user = relationship("User")


class SchoolPermission(Base):
    __tablename__ = "school_permissions"
    __table_args__ = (UniqueConstraint("school_id", "service_name", name="uq_school_permission"),)

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    service_name = Column(String(100), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)

    school = relationship("School", back_populates="permissions")

from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from database.database import Base


class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    first_name = Column(String(255), nullable=False)
    middle_name = Column(String(255))
    last_name = Column(String(255))
    date_of_birth = Column(Date, nullable=False)
    designation = Column(String(255), nullable=False)
    mobile_number = Column(String(20), nullable=False)
    email = Column(String(255), nullable=False)
    spouse_name = Column(String(255))
    father_name = Column(String(255), nullable=False)
    mother_name = Column(String(255), nullable=False)
    nationality = Column(String(100), nullable=False)
    aadhaar_number = Column(String(20), nullable=False)
    religion = Column(String(100))
    caste_category_id = Column(Integer, ForeignKey("caste_categories.id"), nullable=False)
    qualification = Column(String(255))
    joining_date = Column(Date)
    biometric_code = Column(String(100))
    experience = Column(String(255))
    mode_of_transport = Column(String(100))
    salary = Column(Numeric(12, 2))
    blood_group = Column(String(10))
    role = Column(String(100), nullable=False)
    gender = Column(String(50), nullable=False)
    feedback = Column(Text)

    address = relationship("StaffAddress", back_populates="staff", uselist=False,
                           cascade="all, delete-orphan")
    permissions = relationship("StaffPermission", back_populates="staff",
                               cascade="all, delete-orphan", order_by="StaffPermission.id")


class StaffAddress(Base):
    __tablename__ = "staff_address"

    id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, unique=True)
    line_1 = Column(String(500), nullable=False)
    line_2 = Column(String(500))
    city = Column(String(255), nullable=False)
    country = Column(String(100), nullable=False)
    state = Column(String(255), nullable=False)
    pin_code = Column(String(20), nullable=False)

    staff = relationship("Staff", back_populates="address")


class StaffPermission(Base):
    __tablename__ = "staff_permission"
    __table_args__ = (UniqueConstraint("staff_id", "staff_module_id", name="uq_staff_permission_module"),)

    id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True)
    # staff_modules is an existing, reflected table; validate IDs in the API.
    staff_module_id = Column(Integer, nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)

    staff = relationship("Staff", back_populates="permissions")

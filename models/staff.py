from enum import Enum

from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.ext.associationproxy import association_proxy

from database.database import Base


class StaffRole(str, Enum):
    TEACHER = "Teacher"
    PRINCIPAL = "Principal"
    ACCOUNTANT = "Accountant"
    LIBRARIAN = "Librarian"
    RECEPTIONIST = "Receptionist"
    TRANSPORT_STAFF = "Transport Staff"
    SUPPORT_STAFF = "Support Staff"


def login_account_field(name):
    """Read and write common staff fields on the linked login account."""
    def get_value(staff):
        return getattr(staff.login_user, name)

    def set_value(staff, value):
        setattr(staff.login_user, name, value)

    return property(get_value, set_value)


class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True)
    login_user_id = Column(Integer, ForeignKey("login_user.id"), nullable=False, unique=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    login_user = relationship("LoginUser", lazy="joined")
    status = Column(String(20), nullable=False, default="Active")

    first_name = login_account_field("first_name")
    middle_name = login_account_field("middle_name")
    last_name = login_account_field("last_name")
    date_of_birth = Column(Date)
    designation = Column(String(255))
    mobile_number = login_account_field("mobile")
    mobile = association_proxy("login_user", "mobile")
    password = association_proxy("login_user", "password")
    email = login_account_field("email")
    spouse_name = Column(String(255))
    father_name = Column(String(255))
    mother_name = Column(String(255))
    nationality = Column(String(100))
    aadhaar_number = Column(String(20))
    religion = Column(String(100))
    caste_category_id = Column(Integer, ForeignKey("caste_categories.id"))
    qualification = Column(String(255))
    joining_date = Column(Date)
    biometric_code = Column(String(100))
    experience = Column(String(255))
    mode_of_transport = Column(String(100))
    salary = Column(Numeric(12, 2))
    blood_group = Column(String(10))
    role = login_account_field("role")
    gender = Column(String(50))
    feedback = Column(Text)

    address = association_proxy("login_user", "address")
    permissions = property(
        lambda self: self.login_user.permissions,
        lambda self, value: setattr(self.login_user, "permissions", value),
    )


class StaffAddress(Base):
    __tablename__ = "staff_address"
    id = Column(Integer, primary_key=True)
    login_user_id = Column(Integer, ForeignKey("login_user.id", ondelete="CASCADE"),
                           nullable=False, unique=True, index=True)
    line_1 = Column(String(500))
    line_2 = Column(String(500))
    city = Column(String(255))
    country = Column(String(255))
    state = Column(String(255))
    pin_code = Column(String(20))

    login_user = relationship("LoginUser", back_populates="address")


class StaffPermission(Base):
    __tablename__ = "staff_permission"
    __table_args__ = (UniqueConstraint("login_user_id", "staff_module_id", name="uq_staff_permission_module"),)

    id = Column(Integer, primary_key=True)
    login_user_id = Column(Integer, ForeignKey("login_user.id", ondelete="CASCADE"), nullable=False, index=True)
    # staff_modules is an existing, reflected table; validate IDs in the API.
    staff_module_id = Column(Integer, nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)

    login_user = relationship("LoginUser", back_populates="permissions")

from enum import Enum as PyEnum

from sqlalchemy import Column, Enum as SQLAlchemyEnum, Integer, String

from database.database import Base


class UserRole(PyEnum):
    SUPER_ADMIN = "Super Admin"
    ADMIN = "Admin"
    SUB_ADMIN = "Sub Admin"


class UserStatus(PyEnum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    PENDING = "Pending"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(255), nullable=False)
    middle_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    mobile = Column(String(20), nullable=False)
    password = Column(String(255), nullable=False, default="")
    date_of_birth = Column(String(50), nullable=True)
    designation = Column(String(255), nullable=True)
    aadhaar_number = Column(String(255), nullable=True)
    nationality = Column(String(255), nullable=True)
    spouse_name = Column(String(255), nullable=True)
    father_name = Column(String(255), nullable=True)
    mother_name = Column(String(255), nullable=True)
    description = Column(String(1000), nullable=True)
    gender = Column(String(50), nullable=True)

    # Address (Step 2) fields
    line_1 = Column(String(255), nullable=True)
    line_2 = Column(String(255), nullable=True)
    city = Column(String(255), nullable=True)
    country = Column(String(255), nullable=True)
    state = Column(String(255), nullable=True)
    pin_code = Column(String(20), nullable=True)

    school_name = Column(String(255), nullable=True)
    role = Column(
        SQLAlchemyEnum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.ADMIN,
    )
    status = Column(
        SQLAlchemyEnum(UserStatus, name="user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
    )
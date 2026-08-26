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
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    mobile = Column(String, nullable=False)
    school_name = Column(String, nullable=False)
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
from enum import Enum as PyEnum

from sqlalchemy import Column, Enum as SQLAlchemyEnum, ForeignKey, Integer, String
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator

from database.database import Base
from models.staff import StaffAddress as UserAddress


class UserRole(PyEnum):
    SUPER_ADMIN = "Super Admin"
    ADMIN = "Admin"
    SUB_ADMIN = "Sub Admin"

    @classmethod
    def _missing_(cls, value):
        # Accept compact client labels while retaining canonical enum values.
        if isinstance(value, str):
            return {"SuperAdmin": cls.SUPER_ADMIN, "SubAdmin": cls.SUB_ADMIN}.get(value)
        return None


class UserStatus(PyEnum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    PENDING = "Pending"


class AccountRole(TypeDecorator):
    """Keep admin enum behavior while permitting role-specific account labels."""
    impl = String(100)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.value if isinstance(value, PyEnum) else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return UserRole[value] if value in UserRole.__members__ else UserRole(value)
        except ValueError:
            return value


class LoginUser(Base):
    """Shared identity and credentials for user and staff accounts."""

    __tablename__ = "login_user"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(255), nullable=False)
    middle_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    mobile = Column(String(20), nullable=False, unique=True, index=True)
    password = Column(String(255), nullable=False, default="")
    role = Column(
        AccountRole(),
        nullable=False,
        default=UserRole.ADMIN,
    )

    status = Column(
        SQLAlchemyEnum(UserStatus, name="account_status", native_enum=False),
        nullable=False, default=UserStatus.ACTIVE, server_default="ACTIVE",
    )

    address = relationship("StaffAddress", back_populates="login_user", uselist=False,
                           cascade="all, delete-orphan", lazy="joined")
    permissions = relationship("StaffPermission", back_populates="login_user",
                               cascade="all, delete-orphan", order_by="StaffPermission.id")

    line_1 = association_proxy("address", "line_1", creator=lambda value: UserAddress(line_1=value))
    line_2 = association_proxy("address", "line_2", creator=lambda value: UserAddress(line_2=value))
    city = association_proxy("address", "city", creator=lambda value: UserAddress(city=value))
    country = association_proxy("address", "country", creator=lambda value: UserAddress(country=value))
    state = association_proxy("address", "state", creator=lambda value: UserAddress(state=value))
    pin_code = association_proxy("address", "pin_code", creator=lambda value: UserAddress(pin_code=value))





class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login_user_id = Column(Integer, ForeignKey("login_user.id"), nullable=False, unique=True, index=True)
    login_user = relationship("LoginUser", cascade="all, delete-orphan", single_parent=True, lazy="joined")

    first_name = association_proxy("login_user", "first_name", creator=lambda value: LoginUser(first_name=value))
    middle_name = association_proxy("login_user", "middle_name", creator=lambda value: LoginUser(middle_name=value))
    last_name = association_proxy("login_user", "last_name", creator=lambda value: LoginUser(last_name=value))
    email = association_proxy("login_user", "email", creator=lambda value: LoginUser(email=value))
    mobile = association_proxy("login_user", "mobile", creator=lambda value: LoginUser(mobile=value))
    password = association_proxy("login_user", "password", creator=lambda value: LoginUser(password=value))
    role = association_proxy("login_user", "role", creator=lambda value: LoginUser(role=value))

    address = association_proxy("login_user", "address")
    permissions = association_proxy("login_user", "permissions")

    date_of_birth = Column(String(50), nullable=True)
    designation = Column(String(255), nullable=True)
    aadhaar_number = Column(String(255), nullable=True)
    nationality = Column(String(255), nullable=True)
    spouse_name = Column(String(255), nullable=True)
    father_name = Column(String(255), nullable=True)
    mother_name = Column(String(255), nullable=True)
    description = Column(String(1000), nullable=True)
    gender = Column(String(50), nullable=True)

    line_1 = association_proxy("login_user", "line_1")
    line_2 = association_proxy("login_user", "line_2")
    city = association_proxy("login_user", "city")
    country = association_proxy("login_user", "country")
    state = association_proxy("login_user", "state")
    pin_code = association_proxy("login_user", "pin_code")

    status = association_proxy("login_user", "status", creator=lambda value: LoginUser(status=value))

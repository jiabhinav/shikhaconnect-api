from sqlalchemy import Column, Date, ForeignKey, Integer, Enum as SQLAlchemyEnum, Numeric, String, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from database.database import Base
from models.user import UserStatus


class TeacherLogin(Base):
    __tablename__ = "teacher_login"

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    first_name = Column(String(255), nullable=False)
    middle_name = Column(String(255))
    last_name = Column(String(255))
    mobile = Column(String(20), nullable=False, unique=True, index=True)
    email = Column(String(255), unique=True)
    password = Column(String(255), nullable=False)
    role = Column(String(100), nullable=False, default="Teacher", server_default="Teacher")
    status = Column(SQLAlchemyEnum(UserStatus, name="teacher_account_status", native_enum=False),
                    nullable=False, default=UserStatus.ACTIVE, server_default="ACTIVE")

    @property
    def permissions(self):
        return []


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_login_id = Column(Integer, ForeignKey("teacher_login.id"), nullable=False, unique=True)
    teacher_login = relationship("TeacherLogin", lazy="joined")
    first_name = association_proxy("teacher_login", "first_name")
    middle_name = association_proxy("teacher_login", "middle_name")
    last_name = association_proxy("teacher_login", "last_name")
    mobile_number = association_proxy("teacher_login", "mobile")
    email = association_proxy("teacher_login", "email")
    role = association_proxy("teacher_login", "role")
    mobile = association_proxy("teacher_login", "mobile")
    password = association_proxy("teacher_login", "password")
    status = association_proxy("teacher_login", "status")
    date_of_birth = Column(Date, nullable=False)
    father_name = Column(String(255), nullable=False)
    mother_name = Column(String(255), nullable=False)
    spouse_name = Column(String(255))
    gender = Column(String(50), nullable=False)
    alternate_contact_no = Column(String(20))
    aadhaar_number = Column(String(20))
    mode_of_transport = Column(String(100))
    qualification = Column(String(255))
    designation = Column(String(255), nullable=False)
    blood_group = Column(String(10))
    joining_date = Column(Date)
    salary = Column(Numeric(12, 2))
    nationality = Column(String(100), nullable=False)
    caste_category_id = Column(Integer, ForeignKey("caste_categories.id"), nullable=False)
    religion = Column(String(100))
    biometric_code = Column(String(100))
    experience = Column(String(255))
    feedback = Column(Text)
    addresses = relationship("TeacherAddress", back_populates="teacher", cascade="all, delete-orphan",
                             lazy="selectin")

    @property
    def address(self):
        return next((address for address in self.addresses if address.address_type == "present"), None)


class TeacherAddress(Base):
    __tablename__ = "teacher_addresses"
    __table_args__ = (
        UniqueConstraint("teacher_id", "address_type", name="uq_teacher_address_type"),
        CheckConstraint("address_type IN ('present', 'permanent')", name="ck_teacher_address_type"),
    )
    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True)
    address_type = Column(String(20), nullable=False)
    landline_number = Column(String(20))
    district = Column(String(255))
    line_1 = Column(String(500), nullable=False)
    line_2 = Column(String(500))
    city = Column(String(255))
    country = Column(String(100), nullable=False)
    state = Column(String(255), nullable=False)
    pin_code = Column(String(20))
    teacher = relationship("Teacher", back_populates="addresses")

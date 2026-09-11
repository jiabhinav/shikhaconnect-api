from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, func

from database.database import Base


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), nullable=True)
    status = Column(String(8), nullable=False, default="Active", server_default="Active")
    __table_args__ = (
        CheckConstraint("status IN ('Active', 'Inactive')", name="ck_subject_status"),
        Index("uq_subject_school_name", school_id, func.lower(func.trim(name)), unique=True),
        Index("uq_subject_school_code", school_id, func.lower(func.trim(code)), unique=True),
    )

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, func

from database.database import Base


class SchoolClass(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    class_order = Column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint("class_order > 0", name="ck_class_order_positive"),
        Index("uq_class_school_name", school_id, func.lower(func.trim(name)), unique=True),
    )


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    __table_args__ = (
        Index("uq_section_school_name", school_id, func.lower(func.trim(name)), unique=True),
    )

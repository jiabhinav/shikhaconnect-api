from sqlalchemy import Column, ForeignKey, Integer

from database.database import Base


class StudentPromotion(Base):
    __tablename__ = "student_promotions"

    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    source_student_id = Column(Integer, ForeignKey("students.id", ondelete="RESTRICT"), primary_key=True)
    destination_student_id = Column(Integer, ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, unique=True)

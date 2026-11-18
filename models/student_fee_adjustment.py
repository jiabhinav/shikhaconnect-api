from sqlalchemy import CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String

from database.database import Base


class StudentFeeAdjustment(Base):
    __tablename__ = "student_fee_adjustments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_student_fee_adjustment_positive_amount"),
        Index("ix_student_fee_adjustment_scope", "school_id", "session_id", "student_id"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(20), nullable=False)
    frequency = Column(String(20), nullable=False)
    discount_type = Column("name", String(20), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    date = Column(Date, nullable=False)
    description = Column(String(1000), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=True)

from sqlalchemy import (
    CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, and_, cast, func, or_, select,
)
from sqlalchemy.orm import column_property

from database.database import Base
from models.student_carry_forward import StudentCarryForward
from models.student_fee import StudentFee, StudentFeeHead
from schemas.enums import FeeAdjustmentType


class StudentFeeAdjustment(Base):
    __tablename__ = "student_fee_adjustments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_student_fee_adjustment_positive_amount"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= amount", name="ck_fee_adjustment_paid_range"),
        Index("ix_student_fee_adjustment_scope", "school_id", "session_id", "student_id"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    admission_number = Column(String(150), nullable=True)
    type = Column(String(20), nullable=False)
    frequency = Column(String(20), nullable=False)
    discount_type = Column("name", String(20), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), nullable=False, server_default="0.00")
    date = Column(Date, nullable=False)
    description = Column(String(1000), nullable=False)
    tag = Column(String(50), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)


paid_penalty = StudentFeeAdjustment.__table__.alias()
fee_month = func.substr(cast(StudentFeeAdjustment.date, String), 1, 7)
StudentFeeAdjustment.editable = column_property(~or_(
    select(StudentFee.id).where(
        StudentFee.school_id == StudentFeeAdjustment.school_id,
        StudentFee.academic_session_id == StudentFeeAdjustment.session_id,
        StudentFee.student_id == StudentFeeAdjustment.student_id,
        StudentFee.fee_month == fee_month,
        or_(
            StudentFee.paid_amount > 0,
            and_(
                StudentFeeAdjustment.type == FeeAdjustmentType.DISCOUNT,
                or_(
                    StudentFee.discount_amount > 0,
                    select(StudentFeeHead.id).where(
                        StudentFeeHead.student_fee_id == StudentFee.id,
                        StudentFeeHead.discount_amount > 0,
                    ).correlate(StudentFee).exists(),
                ),
            ),
        ),
    ).correlate_except(StudentFee).exists(),
    select(StudentCarryForward.id).where(
        StudentCarryForward.school_id == StudentFeeAdjustment.school_id,
        StudentCarryForward.session_id == StudentFeeAdjustment.session_id,
        StudentCarryForward.student_id == StudentFeeAdjustment.student_id,
        StudentCarryForward.month == fee_month,
        StudentCarryForward.paid_amount > 0,
    ).correlate_except(StudentCarryForward).exists(),
    and_(StudentFeeAdjustment.type == FeeAdjustmentType.PENALTY, StudentFeeAdjustment.applied_at.is_not(None)),
    select(paid_penalty.c.id).where(
        paid_penalty.c.school_id == StudentFeeAdjustment.school_id,
        paid_penalty.c.session_id == StudentFeeAdjustment.session_id,
        paid_penalty.c.student_id == StudentFeeAdjustment.student_id,
        func.substr(cast(paid_penalty.c.date, String), 1, 7) == fee_month,
        paid_penalty.c.paid_amount > 0,
    ).correlate(StudentFeeAdjustment).exists(),
))

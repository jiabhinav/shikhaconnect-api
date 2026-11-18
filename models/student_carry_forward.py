from sqlalchemy import CheckConstraint, Column, Computed, ForeignKey, Index, Integer, Numeric, String

from database.database import Base


CARRY_FORWARD_TAG = "carry_forward"


class StudentCarryForward(Base):
    __tablename__ = "student_carry_forwards"
    __table_args__ = (
        Index(
            "uq_student_carry_forward_school_student_session",
            "school_id", "student_id", "session_id",
            unique=True,
        ),
        CheckConstraint("amount >= 0", name="ck_student_carry_forward_nonnegative_amount"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    month = Column(String(7), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    tag = Column(String(30), nullable=False, default=CARRY_FORWARD_TAG, server_default=CARRY_FORWARD_TAG)
    amount = Column(Numeric(12, 2), nullable=False, server_default="0.00")
    paid_amount = Column(
        Numeric(12, 2),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= amount", name="ck_student_carry_forward_paid_range"),
        nullable=False,
        server_default="0.00",
    )
    unpaid_amount = Column(Numeric(12, 2), Computed("amount - paid_amount"), nullable=False)

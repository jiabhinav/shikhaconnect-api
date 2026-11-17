from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import relationship

from database.database import Base


class StudentFee(Base):
    __tablename__ = "student_fees"
    __table_args__ = (
        UniqueConstraint("school_id", "academic_session_id", "student_id", "fee_month", name="uq_student_fee_month"),
        CheckConstraint("total_amount >= 0 AND discount_amount >= 0 AND fine_amount >= 0 AND paid_amount >= 0", name="ck_student_fee_nonnegative_amounts"),
        CheckConstraint("net_amount >= 0 AND paid_amount <= net_amount", name="ck_student_fee_payment_range"),
        CheckConstraint("status IN ('UNPAID', 'PARTIAL', 'PAID')", name="ck_student_fee_status"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    fee_month = Column(String(7), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0)
    fine_amount = Column(Numeric(12, 2), nullable=False, default=0)
    net_amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), nullable=False, default=0)
    status = Column(String(10), nullable=False, default="UNPAID")
    payment_mode = Column(String(50), nullable=True)
    description = Column(String(1000), nullable=True)
    collected_by = Column(String(255), nullable=True)
    fee_heads = relationship("StudentFeeHead", cascade="all, delete-orphan", order_by="StudentFeeHead.fee_head_id")

    @property
    def due_amount(self):
        return self.net_amount - self.paid_amount


class StudentFeeHead(Base):
    __tablename__ = "student_fee_heads"
    __table_args__ = (
        UniqueConstraint("student_fee_id", "fee_head_id", name="uq_student_fee_head"),
        CheckConstraint("amount >= 0 AND discount_amount >= 0 AND fine_amount >= 0 AND paid_amount >= 0", name="ck_student_fee_head_nonnegative"),
        CheckConstraint("net_amount >= 0 AND paid_amount <= net_amount", name="ck_student_fee_head_payment_range"),
    )

    id = Column(Integer, primary_key=True)
    student_fee_id = Column(Integer, ForeignKey("student_fees.id", ondelete="CASCADE"), nullable=False, index=True)
    fee_head_id = Column(Integer, ForeignKey("fee_heads.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0)
    fine_amount = Column(Numeric(12, 2), nullable=False, default=0)
    net_amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), nullable=False, default=0)
    fee_head = relationship("FeeHead")

    @property
    def fee_head_name(self):
        return self.fee_head.fee_head_name

    @property
    def due_amount(self):
        return self.net_amount - self.paid_amount

from sqlalchemy import (
    JSON, BigInteger, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func,
)
from sqlalchemy.orm import relationship

from database.database import Base


class StudentFeeReceiptCounter(Base):
    __tablename__ = "student_fee_receipt_counters"

    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True)
    session_name = Column(String(100), primary_key=True)
    last_number = Column(BigInteger, nullable=False)


class StudentFeeReceipt(Base):
    __tablename__ = "student_fee_receipts"
    __table_args__ = (
        CheckConstraint("payment_amount > 0", name="ck_student_fee_receipt_amount_positive"),
        Index("ix_student_fee_receipt_scope_date", "school_id", "session_id", "payment_date"),
        Index("ix_student_fee_receipt_scope_number", "school_id", "session_id", "student_id", "receipt_number"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    receipt_number = Column(String(125), nullable=False)
    payment_date = Column(Date, nullable=False)
    payment_at = Column(DateTime(timezone=True), nullable=True)
    payment_amount = Column(Numeric(18, 2), nullable=False)
    payment_mode = Column(String(50), nullable=True)
    collected_by = Column(String(255), nullable=True)
    tag = Column(String(50), nullable=True)
    payment_details = Column(JSON, nullable=True)


class StudentFee(Base):
    __tablename__ = "student_fees"
    __table_args__ = (
        UniqueConstraint("school_id", "academic_session_id", "student_id", "fee_month", name="uq_student_fee_month"),
        CheckConstraint("total_amount >= 0 AND discount_amount >= 0 AND fine_amount >= 0 AND paid_amount >= 0", name="ck_student_fee_nonnegative_amounts"),
        CheckConstraint("net_amount >= 0", name="ck_student_fee_payment_range"),
        CheckConstraint("status IN ('UNPAID', 'PARTIAL', 'PAID')", name="ck_student_fee_status"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    fee_month = Column(String(7), nullable=False)
    total_amount = Column(Numeric(18, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0)
    fine_amount = Column(Numeric(12, 2), nullable=False, default=0)
    net_amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(18, 2), nullable=False, default=0)
    late_fee_amount = Column(Numeric(16, 2), CheckConstraint("late_fee_amount >= 0"), nullable=False, server_default="0")
    status = Column(String(10), nullable=False, default="UNPAID")
    receipt_number = Column(String(125), nullable=True)
    payment_date = Column(DateTime(timezone=True), nullable=True)
    tag = Column(String(50), nullable=True)
    payment_mode = Column(String(50), nullable=True)
    description = Column(String(1000), nullable=True)
    collected_by = Column(String(255), nullable=True)
    fee_heads = relationship("StudentFeeHead", cascade="all, delete-orphan", order_by="StudentFeeHead.fee_head_id")

    @property
    def total_accepted_payment(self):
        return self.paid_amount

    @property
    def due_amount(self):
        return self.net_amount - self.fee_head_paid_amount


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
    # The portion of discount_amount already applied from the fee-adjustment ledger.
    adjustment_discount_amount = Column(Numeric(12, 2), nullable=False, server_default="0")
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


class StudentFeeChequePayment(Base):
    __tablename__ = "student_fee_cheque_payments"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'SUCCESS', 'BOUNCED')", name="ck_fee_cheque_status"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    receipt_number = Column(String(125), nullable=True)
    payment_date = Column(DateTime(timezone=True), nullable=True)
    tag = Column(String(50), nullable=True)
    cheque_number = Column(String(100), nullable=False)
    months = Column(JSON, nullable=False)
    late_fee_payment_amount = Column(Numeric(16, 2), nullable=False, server_default="0")
    description = Column(String(1000), nullable=True)
    collected_by = Column(String(255), nullable=True)
    status = Column(String(10), nullable=False, default="PENDING")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class StudentFeeRazorpayOrder(Base):
    __tablename__ = "student_fee_razorpay_orders"

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    receipt_number = Column(String(125), nullable=True)
    razorpay_order_id = Column(String(100), nullable=False, unique=True)
    razorpay_payment_id = Column(String(100), nullable=True, unique=True)
    amount = Column(BigInteger, nullable=False)
    months = Column(JSON, nullable=False)
    late_fee_payment_amount = Column(Numeric(16, 2), nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    posted_at = Column(DateTime(timezone=True), nullable=True)


class StudentFeeLatePayment(Base):
    __tablename__ = "student_fee_late_payments"
    __table_args__ = (
        UniqueConstraint("school_id", "session_id", "student_id", "fee_month", name="uq_student_fee_late_payment_month"),
        CheckConstraint("amount > 0 AND paid_amount >= 0 AND paid_amount <= amount", name="ck_fee_late_payment_range"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    fee_month = Column(String(7), nullable=False)
    due_date = Column(Date, nullable=False)
    overdue_days = Column(Integer, nullable=False)
    amount = Column(Numeric(16, 2), nullable=False)
    paid_amount = Column(Numeric(16, 2), nullable=False)

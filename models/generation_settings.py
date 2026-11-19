from sqlalchemy import Boolean, CheckConstraint, Column, Date, Enum, ForeignKey, Integer, Numeric, func
from database.database import Base
from schemas.enums import FeeType, LateFeeType


late_fee_type_enum = Enum(
    LateFeeType,
    name="late_fee_type",
    native_enum=False,
    create_constraint=True,
    values_callable=lambda members: [member.value for member in members],
)


class FeeGenerationSettings(Base):
    __tablename__ = "fee_generation_settings"
    fee_type = FeeType.SCHOOL_FEE
    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    generation_day = Column(Integer, nullable=False)
    payment_due_days = Column(Integer, nullable=False)
    late_fee_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    late_fee_type = Column(late_fee_type_enum)
    late_fee_amount = Column(Numeric(12, 2))
    min_due_amount = Column(Integer)
    created_at = Column(Date, default=func.current_date())
    updated_at = Column(Date, default=func.current_date(), onupdate=func.current_date())
    __table_args__ = (
        CheckConstraint("generation_day BETWEEN 1 AND 31", name="ck_fee_generation_day"),
        CheckConstraint("late_fee_amount > 0", name="ck_fee_late_fee_amount"),
        CheckConstraint("min_due_amount BETWEEN 1 AND 100", name="ck_fee_min_due_amount"),
        CheckConstraint("payment_due_days >= 0", name="ck_fee_due_days"),
    )


class TransportGenerationSettings(Base):
    __tablename__ = "transport_generation_settings"
    fee_type = FeeType.TRANSPORT
    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    generation_day = Column(Integer, nullable=False)
    payment_due_day = Column(Integer, nullable=False)
    late_fee_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    late_fee_type = Column(late_fee_type_enum)
    late_fee_amount = Column(Numeric(12, 2))
    min_due_amount = Column(Integer)
    created_at = Column(Date, default=func.current_date())
    updated_at = Column(Date, default=func.current_date(), onupdate=func.current_date())
    __table_args__ = (
        CheckConstraint("generation_day BETWEEN 1 AND 31", name="ck_transport_generation_day"),
        CheckConstraint("late_fee_amount > 0", name="ck_transport_late_fee_amount"),
        CheckConstraint("min_due_amount BETWEEN 1 AND 100", name="ck_transport_min_due_amount"),
        CheckConstraint("payment_due_day BETWEEN 1 AND 31", name="ck_transport_due_day"),
    )

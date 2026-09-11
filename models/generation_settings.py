from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer
from database.database import Base


class FeeGenerationSettings(Base):
    __tablename__ = "fee_generation_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    generation_day = Column(Integer, nullable=False)
    payment_due_days = Column(Integer, nullable=False)
    late_fee_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    __table_args__ = (
        CheckConstraint("generation_day BETWEEN 1 AND 31", name="ck_fee_generation_day"),
        CheckConstraint("payment_due_days >= 0", name="ck_fee_due_days"),
    )


class TransportGenerationSettings(Base):
    __tablename__ = "transport_generation_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    generation_day = Column(Integer, nullable=False)
    payment_due_day = Column(Integer, nullable=False)
    late_fee_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    __table_args__ = (
        CheckConstraint("generation_day BETWEEN 1 AND 31", name="ck_transport_generation_day"),
        CheckConstraint("payment_due_day BETWEEN 1 AND 31", name="ck_transport_due_day"),
    )

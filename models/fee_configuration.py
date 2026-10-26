from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, JSON, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship

from database.database import Base


class FeeConfiguration(Base):
    __tablename__ = "fee_configurations"

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    fee_structure_id = Column(Integer, ForeignKey("fee_structures.id", ondelete="RESTRICT"), nullable=False)
    fee_category_id = Column(Integer, ForeignKey("fee_categories.id", ondelete="RESTRICT"), nullable=False)
    session = relationship("Session")
    fee_heads = relationship("FeeConfigurationHead", back_populates="configuration", cascade="all, delete-orphan", order_by="FeeConfigurationHead.id")
    __table_args__ = (
        UniqueConstraint("school_id", "session_id", "fee_structure_id", "fee_category_id", name="uq_fee_configuration_scope"),
    )

    @property
    def no_of_fee_heads(self):
        return len(self.fee_heads)


class FeeConfigurationHead(Base):
    __tablename__ = "fee_configuration_heads"

    id = Column(Integer, primary_key=True)
    configuration_id = Column(Integer, ForeignKey("fee_configurations.id", ondelete="CASCADE"), nullable=False, index=True)
    fee_head_id = Column(Integer, ForeignKey("fee_heads.id", ondelete="RESTRICT"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    april_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    may_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    june_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    july_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    august_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    september_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    october_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    november_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    december_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    january_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    february_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    march_fee = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    # YYYY-MM keys store decimal strings or {amount, enabled} month settings.
    month_amounts = Column(JSON, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("configuration_id", "fee_head_id", name="uq_fee_configuration_head"),
        CheckConstraint("amount >= 0", name="ck_fee_configuration_amount"),
    )

    configuration = relationship("FeeConfiguration", back_populates="fee_heads")

    @property
    def complete_month_amounts(self):
        from utils.fee_months import session_months
        session = self.configuration.session
        values = {month: self.month_amounts.get(month, self.amount)
                  for month in session_months(session.start_date, session.end_date)}
        return {month: {**value, 'amount': None} if isinstance(value, dict) and not value['enabled'] else value
                for month, value in values.items()}

    @property
    def edit_months_wise(self):
        return bool(self.month_amounts)

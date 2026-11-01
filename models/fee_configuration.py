from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, Numeric, String, UniqueConstraint, false, true
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
        return len({head.fee_head_id for head in self.fee_heads})


class FeeConfigurationHead(Base):
    __tablename__ = "fee_configuration_heads"

    id = Column(Integer, primary_key=True)
    configuration_id = Column(Integer, ForeignKey("fee_configurations.id", ondelete="CASCADE"), nullable=False, index=True)
    fee_head_id = Column(Integer, ForeignKey("fee_heads.id", ondelete="RESTRICT"), nullable=False)
    base_amount = Column(Numeric(12, 2), nullable=False)
    month = Column(String(7), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default=true())
    is_override = Column(Boolean, nullable=False, default=False, server_default=false())
    has_checkbox = Column(Boolean, nullable=False, default=False, server_default=false())
    __table_args__ = (
        UniqueConstraint("configuration_id", "fee_head_id", "month", name="uq_fee_configuration_head_month"),
        CheckConstraint("amount >= 0", name="ck_fee_configuration_amount"),
        CheckConstraint("base_amount >= 0", name="ck_fee_configuration_base_amount"),
    )

    configuration = relationship("FeeConfiguration", back_populates="fee_heads")


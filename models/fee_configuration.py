from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import relationship

from database.database import Base


class FeeConfiguration(Base):
    __tablename__ = "fee_configurations"

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    fee_structure_id = Column(Integer, ForeignKey("fee_structures.id", ondelete="RESTRICT"), nullable=False)
    fee_category_id = Column(Integer, ForeignKey("fee_categories.id", ondelete="RESTRICT"), nullable=False)
    fee_head_settings = Column(JSON, nullable=False, default=dict, server_default="{}")
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
    month = Column(String(7), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    __table_args__ = (
        UniqueConstraint("configuration_id", "fee_head_id", "month", name="uq_fee_configuration_head_month"),
        CheckConstraint("amount >= 0", name="ck_fee_configuration_amount"),
    )

    configuration = relationship("FeeConfiguration", back_populates="fee_heads")


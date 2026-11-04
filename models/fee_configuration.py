from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from database.database import Base


class FeeConfigurationHead(Base):
    """One row per configured fee head; configuration metadata is repeated by group."""
    __tablename__ = "fee_configuration_heads"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    # Shared group ID for all fee-head rows in one configuration. It is not a
    # foreign key because the configuration is represented by these same rows.
    configuration_id = Column(BigInteger, nullable=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    fee_structure_id = Column(BigInteger, ForeignKey("fee_structures.id", ondelete="RESTRICT"), nullable=False, index=True)
    fee_category_id = Column(Integer, ForeignKey("fee_categories.id", ondelete="RESTRICT"), nullable=False)
    fee_head_id = Column(BigInteger, ForeignKey("fee_heads.id", ondelete="RESTRICT"), nullable=False)
    base_amount = Column(Numeric(12, 2), nullable=False)
    edit_months_wise = Column(Boolean, nullable=False, default=False)
    monthly_config = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    fee_structure = relationship("FeeStructure")
    fee_category = relationship("FeeCategory")

    __table_args__ = (
        UniqueConstraint("configuration_id", "fee_head_id", name="uq_fee_configuration_head"),
        UniqueConstraint("school_id", "session_id", "fee_structure_id", "fee_category_id", "fee_head_id",
                         name="uq_fee_configuration_scope_head"),
        CheckConstraint("base_amount >= 0", name="ck_fee_configuration_base_amount"),
    )


# Kept as a Python alias for older imports; no second database table exists.
FeeConfiguration = FeeConfigurationHead

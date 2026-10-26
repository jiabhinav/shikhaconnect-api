from sqlalchemy import Column, ForeignKey, Index, Integer, String, func

from database.database import Base


class FeeStructure(Base):
    __tablename__ = "fee_structures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    fee_structure_name = Column(String(100), nullable=False)
    __table_args__ = (
        Index("uq_fee_structure_school_session_name", school_id, session_id, func.lower(func.trim(fee_structure_name)), unique=True),
    )

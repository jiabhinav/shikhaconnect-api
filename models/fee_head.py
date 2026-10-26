from sqlalchemy import Column, ForeignKey, Index, Integer, String, func

from database.database import Base


class FeeHead(Base):
    __tablename__ = "fee_heads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    fee_head_name = Column(String(100), nullable=False)
    student_type = Column(String(100), nullable=False)
    __table_args__ = (
        Index("uq_fee_head_school_session_name_type", school_id, session_id, func.lower(func.trim(fee_head_name)), func.lower(func.trim(student_type)), unique=True),
    )

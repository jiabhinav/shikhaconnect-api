from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint

from database.database import Base


class SessionMonth(Base):
    """A selectable payment month within a school's academic session."""
    __tablename__ = "session_months"
    __table_args__ = (
        UniqueConstraint("school_id", "month", name="uq_school_session_month"),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    month = Column(String(7), nullable=False)  # YYYY-MM, e.g. 2026-04

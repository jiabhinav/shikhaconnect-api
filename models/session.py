from datetime import date

from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database.database import Base


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_session_dates"),
    )

    school = relationship("School")

    @property
    def status(self):
        today = date.today()
        if self.end_date < today:
            return "Past"
        if self.start_date > today:
            return "Upcoming"
        return "Current"

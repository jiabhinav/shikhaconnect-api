from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database.database import Base
from utils.dates import today as get_today


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
        today = get_today()
        if today < self.start_date:
            return "Upcoming"
        if self.start_date <= today <= self.end_date:
            return "Current"
        return "Past"

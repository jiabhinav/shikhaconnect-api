from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, Numeric, Time
from database.database import Base


class TimetableSettings(Base):
    __tablename__ = "timetable_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    summer_start_time = Column(Time, nullable=False)
    summer_end_time = Column(Time, nullable=False)
    winter_start_time = Column(Time, nullable=False)
    winter_end_time = Column(Time, nullable=False)
    minimum_attendance_percentage = Column(Numeric(5, 2), nullable=False)
    term_attendance_enabled = Column(Boolean, nullable=False)
    __table_args__ = (
        CheckConstraint("summer_end_time > summer_start_time", name="ck_timetable_summer_times"),
        CheckConstraint("winter_end_time > winter_start_time", name="ck_timetable_winter_times"),
        CheckConstraint("minimum_attendance_percentage BETWEEN 0 AND 100", name="ck_timetable_attendance"),
    )

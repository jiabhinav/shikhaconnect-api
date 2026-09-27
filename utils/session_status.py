from datetime import date
from typing import Literal

from utils.dates import today


class SessionStatus:
    """Shared session date rules; start and end dates are inclusive."""

    @staticmethod
    def is_current(start_date, end_date, *, as_of: date | None = None):
        """Accept dates or SQLAlchemy columns for the same current-date check."""
        current_date = as_of if as_of is not None else today()
        return (start_date <= current_date) & (end_date >= current_date)

    @classmethod
    def classify(cls, start_date: date, end_date: date, *, as_of: date | None = None
                 ) -> Literal["Past", "Current", "Future"]:
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        current_date = as_of if as_of is not None else today()
        if cls.is_current(start_date, end_date, as_of=current_date):
            return "Current"
        return "Future" if current_date < start_date else "Past"

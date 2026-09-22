from datetime import date
import test_sessions
from models.session import Session as SchoolSession


def setUp(self):
    test_sessions.SessionTests.setUp(self)
    self.db.add_all([
        SchoolSession(id=1, school_id=1, name='First', start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
        SchoolSession(id=2, school_id=2, name='Other school', start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
        SchoolSession(id=3, school_id=1, name='Next', start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
    ])
    self.db.commit()

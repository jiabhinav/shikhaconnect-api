import unittest
from datetime import date
from unittest.mock import patch

from models.session import Session
from utils.session_status import SessionStatus


class SessionStatusTests(unittest.TestCase):
    def test_boundaries_and_model_status(self):
        start, end = date(2026, 4, 1), date(2027, 3, 31)
        for current, expected in (
            (date(2026, 3, 31), 'Future'),
            (start, 'Current'),
            (date(2026, 9, 28), 'Current'),
            (end, 'Current'),
            (date(2027, 4, 1), 'Past'),
        ):
            with self.subTest(current=current):
                self.assertEqual(SessionStatus.classify(start, end, as_of=current), expected)
                self.assertEqual(SessionStatus.is_current(start, end, as_of=current), expected == 'Current')
                with patch('models.session.get_today', return_value=current):
                    self.assertEqual(Session(start_date=start, end_date=end).status, expected)

    def test_same_day_and_default_clock(self):
        day = date(2026, 9, 28)
        with patch('utils.session_status.today', return_value=day):
            self.assertEqual(SessionStatus.classify(day, day), 'Current')

    def test_invalid_range(self):
        with self.assertRaises(ValueError):
            SessionStatus.classify(date(2027, 1, 1), date(2026, 1, 1))

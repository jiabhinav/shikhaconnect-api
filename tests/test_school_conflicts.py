import unittest
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from routers.schools import _integrity_error_detail as school_detail
from routers.super_admin import _integrity_error_detail as admin_detail


class SchoolConflictTests(unittest.TestCase):
    def test_database_duplicate_formats(self):
        info = SimpleNamespace(school_code="SCH-001")
        cases = [
            ('Duplicate entry \'SCH-001\' for key \'schools.school_code\'', "school_code", "SCH-001"),
            ('duplicate key value violates unique constraint "schools_school_code_key"\nDETAIL: Key (school_code)=(SCH-001) already exists.', "school_code", "SCH-001"),
            ('UNIQUE constraint failed: schools.school_code', "school_code", "SCH-001"),
        ]
        for handler in (school_detail, admin_detail):
            for message, field, value in cases:
                with self.subTest(handler=handler.__module__, message=message):
                    result = handler(IntegrityError("", {}, Exception(message)), info)
                    self.assertEqual(result["fields"], {field: value})

    def test_postgres_diagnostics_and_composite_value(self):
        original = Exception("duplicate key")
        original.diag = SimpleNamespace(
            message_detail="Key (school_id, start_date, end_date)=(12, 2026-04-01, 2027-03-31) already exists.",
            constraint_name="uq_session_school_years",
        )
        for handler in (school_detail, admin_detail):
            result = handler(IntegrityError("", {}, original), SimpleNamespace())
            self.assertEqual(result["constraint"], "uq_session_school_years")
            self.assertEqual(result["field"], "school_id, start_date, end_date")
            self.assertEqual(result["value"], "12, 2026-04-01, 2027-03-31")

    def test_unknown_error_does_not_invent_conflicting_value(self):
        for handler in (school_detail, admin_detail):
            result = handler(IntegrityError("", {}, Exception("unknown failure")), SimpleNamespace())
            self.assertIsNone(result["value"])

    def test_missing_required_column_is_not_reported_as_duplicate(self):
        original = Exception("not null violation")
        original.pgcode = "23502"
        original.diag = SimpleNamespace(
            message_detail="Failing row contains private values",
            table_name="schools", column_name="legacy_required_column",
            constraint_name=None,
        )
        for handler in (school_detail, admin_detail):
            result = handler(IntegrityError("", {}, original), SimpleNamespace())
            self.assertEqual(result["message"], "A required database field is missing")
            self.assertEqual(result["field"], "legacy_required_column")
            self.assertEqual(result["table"], "schools")
            self.assertEqual(result["code"], "23502")
            self.assertNotIn("private values", str(result))

import unittest
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError
from routers.staff import staff_constraint_error


class StaffConstraintErrorsTests(unittest.TestCase):
    def test_returns_constraint_identifiers_without_values(self):
        for code in ("23505", "23503", "23502", "23514"):
            with self.subTest(code=code):
                original = Exception("sensitive database detail")
                original.pgcode = code
                original.diag = SimpleNamespace(
                    table_name="staff", column_name="first_name",
                    constraint_name="staff_constraint",
                )
                error = staff_constraint_error(IntegrityError(
                    "INSERT ...", {"password": "secret"}, original,
                ))
                self.assertEqual(error.status_code, 409)
                self.assertEqual(error.detail["code"], code)
                self.assertEqual(error.detail["table"], "staff")
                self.assertEqual(error.detail["column"], "first_name")
                self.assertEqual(error.detail["constraint"], "staff_constraint")
                self.assertNotIn("secret", str(error.detail))
                self.assertNotIn("sensitive", str(error.detail))

    def test_unknown_error_preserves_generic_response(self):
        error = staff_constraint_error(IntegrityError("INSERT", {}, Exception("private")))
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.detail, "Staff references conflict with database constraints")

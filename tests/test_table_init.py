import unittest

from sqlalchemy import create_engine, inspect, text
from database.table_init import ensure_all_tables
from database.database import Base


class TableInitializationTests(unittest.TestCase):
    def test_empty_database_and_repeated_initialization(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                ensure_all_tables(connection)
                self.assertNotIn("school_user_assignments", inspect(connection).get_table_names())
                expected = {"login_user", "users", "schools", "school_permissions", "school_mapping", "sessions", "classes", "sections", "subjects"}
                self.assertTrue(expected.issubset(set(inspect(connection).get_table_names())))
                connection.execute(text("INSERT INTO login_user (id, first_name, last_name, email, mobile, password, role) VALUES (1, 'Test', 'User', 'test@example.com', '12345', '', 'ADMIN')"))
                connection.execute(text("INSERT INTO users (id, login_user_id, status) VALUES (1, 1, 'ACTIVE')"))
                ensure_all_tables(connection)
                self.assertEqual(connection.execute(text("SELECT count(*) FROM users")).scalar(), 1)
                Base.metadata.tables["subjects"].drop(connection)
                ensure_all_tables(connection)
                self.assertTrue(inspect(connection).has_table("subjects"))
                self.assertEqual(connection.execute(text("SELECT count(*) FROM users")).scalar(), 1)
        finally:
            engine.dispose()

    def test_missing_login_table_is_created_with_existing_profile_table(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                ensure_all_tables(connection)
                Base.metadata.tables["login_user"].drop(connection)
                self.assertTrue(inspect(connection).has_table("users"))
                self.assertFalse(inspect(connection).has_table("login_user"))

                ensure_all_tables(connection)
                self.assertTrue(inspect(connection).has_table("login_user"))
                ensure_all_tables(connection)
        finally:
            engine.dispose()

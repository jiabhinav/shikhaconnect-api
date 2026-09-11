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
                expected = {"users", "schools", "school_user_assignments", "school_permissions", "sessions", "classes", "sections", "subjects"}
                self.assertTrue(expected.issubset(set(inspect(connection).get_table_names())))
                connection.execute(text("INSERT INTO users (id, first_name, last_name, email, mobile, password, role, status) VALUES (1, 'Test', 'User', 'test@example.com', '12345', '', 'ADMIN', 'ACTIVE')"))
                ensure_all_tables(connection)
                self.assertEqual(connection.execute(text("SELECT count(*) FROM users")).scalar(), 1)
                Base.metadata.tables["subjects"].drop(connection)
                ensure_all_tables(connection)
                self.assertTrue(inspect(connection).has_table("subjects"))
                self.assertEqual(connection.execute(text("SELECT count(*) FROM users")).scalar(), 1)
        finally:
            engine.dispose()

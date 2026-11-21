import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from database import database
from database.table_init import ensure_all_tables
from database.database import Base


class TableInitializationTests(unittest.TestCase):
    def test_student_fee_late_amount_migration_backfills_collected_amounts(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            ensure_all_tables(connection)
            connection.exec_driver_sql("ALTER TABLE student_fees DROP COLUMN late_fee_amount")
            connection.exec_driver_sql("""
                INSERT INTO student_fees (
                    school_id, academic_session_id, student_id, fee_month,
                    total_amount, discount_amount, fine_amount, net_amount, paid_amount, status
                ) VALUES (1, 1, 1, '2025-01', 100, 0, 0, 100, 100, 'PAID')
            """)
            connection.exec_driver_sql("""
                INSERT INTO student_fee_late_payments (
                    school_id, session_id, student_id, fee_month, due_date, overdue_days, amount, paid_amount
                ) VALUES (1, 1, 1, '2025-01', '2025-01-10', 10, 50, 25),
                         (1, 1, 1, '2025-02', '2025-02-10', 10, 50, 40)
            """)
            ensure_all_tables(connection)
            ensure_all_tables(connection)
            rows = connection.exec_driver_sql("""
                SELECT fee_month, paid_amount, late_fee_amount FROM student_fees ORDER BY fee_month
            """).all()
            self.assertEqual(rows, [('2025-01', 100, 25), ('2025-02', 0, 40)])

    def test_database_dependency_retries_failed_initialization_and_runs_it_once(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with (
            patch.object(database, "engine", engine),
            patch.object(database, "SessionLocal", sessionmaker(bind=engine)),
            patch.object(database, "_initialized_engine", None),
            patch("database.table_init.ensure_all_tables", side_effect=[
                OperationalError("startup", {}, Exception("unavailable")),
                None,
            ]) as initialize,
            patch("database.student_carry_forward_table.backfill_student_carry_forwards"),
            patch.object(database, "sync_missing_columns") as sync,
        ):
            with self.assertLogs("database.database", level="ERROR"):
                with self.assertRaises(HTTPException) as error:
                    next(database.get_db())
            self.assertEqual(error.exception.status_code, 503)

            for _ in range(3):
                dependency = database.get_db()
                try:
                    self.assertEqual(next(dependency).execute(text("SELECT 1")).scalar_one(), 1)
                finally:
                    dependency.close()
            self.assertEqual(initialize.call_count, 2)
            sync.assert_called_once()

    def test_staff_permission_table_and_missing_action_columns_are_created(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                ensure_all_tables(connection)
                Base.metadata.tables["staff_permission"].drop(connection)
                ensure_all_tables(connection)
                columns = {c["name"] for c in inspect(connection).get_columns("staff_permission")}
                self.assertTrue({"read", "delete", "update", "create"}.issubset(columns))
                connection.execute(text(
                    "INSERT INTO login_user (id, first_name, email, mobile, password, role) "
                    "VALUES (1, 'Test', 'test@example.com', '12345', '', 'Teacher')"
                ))
                connection.execute(text(
                    'INSERT INTO staff_permission (login_user_id, staff_module_id, is_enabled, "read") '
                    'VALUES (1, 1, true, false)'
                ))
                for name in ("delete", "update", "create"):
                    connection.execute(text(f'ALTER TABLE staff_permission DROP COLUMN "{name}"'))
                ensure_all_tables(connection)
                ensure_all_tables(connection)
                row = connection.execute(text('SELECT * FROM staff_permission')).mappings().one()
                for name in ("read", "delete", "update", "create"):
                    self.assertFalse(row[name])
                connection.execute(text('ALTER TABLE staff_permission DROP COLUMN "read"'))
                ensure_all_tables(connection)
                self.assertTrue(connection.execute(text('SELECT "read" FROM staff_permission')).scalar_one())
        finally:
            engine.dispose()

    def test_empty_database_and_repeated_initialization(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                ensure_all_tables(connection)
                self.assertNotIn("school_user_assignments", inspect(connection).get_table_names())
                self.assertIn("status", {c["name"] for c in inspect(connection).get_columns("login_user")})
                for table in ("users", "staff"):
                    self.assertNotIn("status", {c["name"] for c in inspect(connection).get_columns(table)})
                expected = {"login_user", "users", "schools", "school_permissions", "school_mapping", "sessions", "classes", "sections", "subjects", "teachers", "teacher_addresses", "teacher_login"}
                self.assertTrue(expected.issubset(set(inspect(connection).get_table_names())))
                connection.execute(text("INSERT INTO login_user (id, first_name, last_name, email, mobile, password, role) VALUES (1, 'Test', 'User', 'test@example.com', '12345', '', 'ADMIN')"))
                connection.execute(text("INSERT INTO users (id, login_user_id) VALUES (1, 1)"))
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

    def test_legacy_user_school_name_is_removed_and_repeatable(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                ensure_all_tables(connection)
                connection.execute(text("ALTER TABLE users ADD COLUMN school_name VARCHAR(255)"))
                ensure_all_tables(connection)
                ensure_all_tables(connection)
                self.assertNotIn("school_name", {c["name"] for c in inspect(connection).get_columns("users")})
                self.assertIn("school_name", {c["name"] for c in inspect(connection).get_columns("schools")})
        finally:
            engine.dispose()

    def test_all_fee_tables_are_created_and_recreated(self):
        from utils.fee_months import MONTH_COLUMNS

        engine = create_engine("sqlite://")
        fee_tables = {"fee_categories", "fee_heads", "fee_structures",
                      "fee_configurations", "fee_configuration_heads"}
        try:
            with engine.begin() as connection:
                ensure_all_tables(connection)
                self.assertTrue(fee_tables.issubset(inspect(connection).get_table_names()))
                connection.execute(text(
                    "INSERT INTO login_user (id, first_name, email, mobile, password, role) "
                    "VALUES (1, 'Test', 'fee@example.com', '98765', '', 'ADMIN')"
                ))
                for table in reversed(Base.metadata.sorted_tables):
                    if table.name in fee_tables:
                        table.drop(connection)
                self.assertTrue(fee_tables.isdisjoint(inspect(connection).get_table_names()))
                ensure_all_tables(connection)
                ensure_all_tables(connection)
                self.assertTrue(fee_tables.issubset(inspect(connection).get_table_names()))
                columns = {col['name'] for col in inspect(connection).get_columns('fee_configuration_heads')}
                self.assertTrue(set(MONTH_COLUMNS.values()).issubset(columns))
                self.assertEqual(connection.execute(text('SELECT count(*) FROM login_user')).scalar_one(), 1)
                # Recreate each table individually as well as the whole group.
                for name in fee_tables:
                    Base.metadata.tables[name].drop(connection)
                    ensure_all_tables(connection)
                    self.assertTrue(inspect(connection).has_table(name))
        finally:
            engine.dispose()

"""PostgreSQL integration checks; set LOGIN_MIGRATION_TEST_URL to a test database."""
import os
import unittest
import uuid

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from database.table_init import migrate_legacy_users, register_models
from database.user_address_table import migrate_user_addresses


@unittest.skipUnless(os.environ.get("LOGIN_MIGRATION_TEST_URL"), "Requires a test PostgreSQL database")
class LoginMigrationTests(unittest.TestCase):
    def setUp(self):
        register_models()
        self.engine = create_engine(os.environ["LOGIN_MIGRATION_TEST_URL"])
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        schema = "migration_test_" + uuid.uuid4().hex
        self.connection.execute(text(f"CREATE SCHEMA {schema}"))
        self.connection.execute(text(f"SET LOCAL search_path TO {schema}"))
        self.connection.execute(text("""
            CREATE TYPE user_role AS ENUM ('ADMIN', 'SUPER_ADMIN', 'SUB_ADMIN');
            CREATE TABLE users (
                id SERIAL PRIMARY KEY, first_name VARCHAR(255) NOT NULL,
                middle_name VARCHAR(255), last_name VARCHAR(255) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL, mobile VARCHAR(20) NOT NULL,
                password VARCHAR(255) NOT NULL, role user_role NOT NULL,
                city VARCHAR(255)
            );
            INSERT INTO users VALUES
                (7, 'Test', NULL, 'User', 'test@example.com', '123', 'existing-hash', 'ADMIN', 'Delhi');
            CREATE TABLE staff (id SERIAL PRIMARY KEY);
            CREATE TABLE assignments (user_id INTEGER REFERENCES users(id));
            INSERT INTO assignments VALUES (7);
        """))

    def tearDown(self):
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def test_preserves_data_foreign_keys_and_sequence_and_can_repeat(self):
        migrate_legacy_users(self.connection)
        migrate_legacy_users(self.connection)
        row = self.connection.execute(text("""
            SELECT u.id, u.city, l.id, l.email, l.password
            FROM users u JOIN login_user l ON u.login_user_id = l.id
        """)).one()
        self.assertEqual(tuple(row), (7, "Delhi", 7, "test@example.com", "existing-hash"))
        self.assertEqual(self.connection.execute(text("SELECT user_id FROM assignments")).scalar(), 7)
        self.assertNotIn("password", {col["name"] for col in inspect(self.connection).get_columns("users")})
        foreign_keys = inspect(self.connection).get_foreign_keys("users")
        self.assertTrue(any(fk["referred_table"] == "login_user" for fk in foreign_keys))
        next_id = self.connection.execute(text("""
            INSERT INTO login_user (first_name, last_name, email, mobile, password, role)
            VALUES ('New', 'User', 'new@example.com', '456', 'hash', 'ADMIN') RETURNING id
        """)).scalar()
        self.assertGreater(next_id, 7)

    def test_duplicate_mobile_rolls_back_without_losing_legacy_data(self):
        self.connection.execute(text("""
            INSERT INTO users VALUES
                (8, 'Other', NULL, 'User', 'other@example.com', '123', 'other-hash', 'ADMIN', NULL)
        """))
        with self.assertRaises(IntegrityError):
            with self.connection.begin_nested():
                migrate_legacy_users(self.connection)
        self.assertFalse(inspect(self.connection).has_table("login_user"))
        self.assertEqual(self.connection.execute(text("SELECT count(*) FROM users")).scalar(), 2)
        self.assertEqual(self.connection.execute(text("SELECT password FROM users WHERE id=7")).scalar(), "existing-hash")

    def test_legacy_varchar_roles_are_converted_to_enum(self):
        self.connection.execute(text(
            "ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(255) USING role::text"
        ))
        labels = ["SUPER_ADMIN", "ADMIN", "SUB_ADMIN", "Super Admin",
                  "SuperAdmin", "Admin", "Sub Admin", "SubAdmin"]
        expected = ["SUPER_ADMIN", "ADMIN", "SUB_ADMIN", "SUPER_ADMIN",
                    "SUPER_ADMIN", "ADMIN", "SUB_ADMIN", "SUB_ADMIN"]
        for user_id, label in enumerate(labels, start=10):
            self.connection.execute(text("""
                INSERT INTO users (id, first_name, last_name, email, mobile, password, role)
                VALUES (:id, 'Test', 'User', :email, :mobile, 'hash', :role)
            """), dict(id=user_id, email=f"user{user_id}@example.com",
                       mobile=str(user_id), role=label))
        migrate_legacy_users(self.connection)
        roles = self.connection.execute(text(
            "SELECT role::text FROM login_user WHERE id >= 10 ORDER BY id"
        )).scalars().all()
        self.assertEqual(roles, expected)

    def test_address_migration_preserves_values_and_can_repeat(self):
        self.connection.execute(text("""
            ALTER TABLE users ADD COLUMN line_1 VARCHAR(255);
            ALTER TABLE users ADD COLUMN line_2 VARCHAR(255);
            ALTER TABLE users ADD COLUMN country VARCHAR(255);
            ALTER TABLE users ADD COLUMN state VARCHAR(255);
            ALTER TABLE users ADD COLUMN pin_code VARCHAR(20);
            UPDATE users SET line_1='First street', country='India',
                             state='Delhi', pin_code='110001' WHERE id=7;
        """))
        migrate_legacy_users(self.connection)
        migrate_user_addresses(self.connection)
        migrate_user_addresses(self.connection)
        address = self.connection.execute(text("""
            SELECT login_user_id, line_1, line_2, city, country, state, pin_code
            FROM staff_address
        """)).one()
        self.assertEqual(tuple(address), (7, "First street", None, "Delhi", "India", "Delhi", "110001"))
        self.assertNotIn("city", {col["name"] for col in inspect(self.connection).get_columns("users")})
        self.connection.execute(text("DELETE FROM assignments"))
        self.connection.execute(text("DELETE FROM users WHERE id=7"))
        self.connection.execute(text("DELETE FROM login_user WHERE id=7"))
        self.assertEqual(self.connection.execute(text("SELECT count(*) FROM staff_address")).scalar(), 0)

    def test_merges_misspelled_table_with_existing_staff_addresses(self):
        migrate_legacy_users(self.connection)
        self.connection.execute(text("""
            ALTER TABLE users DROP COLUMN city;
            ALTER TABLE staff ADD COLUMN login_user_id INTEGER REFERENCES login_user(id);
            INSERT INTO login_user (id, first_name, last_name, email, mobile, password, role)
            VALUES (8, 'Staff', 'User', 'staff@example.com', '456', 'hash', 'ADMIN');
            INSERT INTO users (id, login_user_id) VALUES (8, 8);
            INSERT INTO staff (id, login_user_id) VALUES (1, 8);
            CREATE TABLE staff_address (
                id SERIAL PRIMARY KEY,
                staff_id INTEGER NOT NULL UNIQUE REFERENCES staff(id),
                line_1 VARCHAR(500) NOT NULL, line_2 VARCHAR(500),
                city VARCHAR(255) NOT NULL, country VARCHAR(100) NOT NULL,
                state VARCHAR(255) NOT NULL, pin_code VARCHAR(20) NOT NULL
            );
            INSERT INTO staff_address (staff_id, line_1, city, country, state, pin_code)
            VALUES (1, 'Staff street', 'Delhi', 'India', 'Delhi', '110001');
            CREATE TABLE staff_addrers (
                id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                line_1 VARCHAR(255), line_2 VARCHAR(255), city VARCHAR(255),
                country VARCHAR(255), state VARCHAR(255), pin_code VARCHAR(20)
            );
            INSERT INTO staff_addrers (user_id, city) VALUES (7, 'Mumbai');
        """))
        migrate_user_addresses(self.connection)
        migrate_user_addresses(self.connection)
        self.assertFalse(inspect(self.connection).has_table("staff_addrers"))
        rows = self.connection.execute(text(
            "SELECT login_user_id, city FROM staff_address ORDER BY id"
        )).all()
        self.assertEqual([tuple(row) for row in rows], [(8, "Delhi"), (7, "Mumbai")])

    def test_legacy_staff_profiles_move_to_users_and_accounts(self):
        self.check_staff_profile_migration(partial=False)

    def test_partial_staff_migration_resumes_unlinked_rows(self):
        self.check_staff_profile_migration(partial=True)

    def check_staff_profile_migration(self, partial):
        from database.staff_account_table import migrate_staff_accounts, STAFF_PROFILE_FIELDS, PROFILE_COLUMNS
        from utils.passwords import verify_password
        migrate_legacy_users(self.connection)
        # Build the legacy profile columns around this fixture's minimal tables.
        self.connection.execute(text("""
            CREATE TABLE caste_categories (id INTEGER PRIMARY KEY);
            CREATE TABLE school_mapping (
                id SERIAL PRIMARY KEY, school_id INTEGER, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                status VARCHAR(20)
            );
            ALTER TABLE users ADD COLUMN status VARCHAR(20);
            ALTER TABLE staff ADD COLUMN school_id INTEGER;
        """))
        common = {
            "date_of_birth": "VARCHAR(50)", "designation": "VARCHAR(255)",
            "spouse_name": "VARCHAR(255)", "father_name": "VARCHAR(255)",
            "mother_name": "VARCHAR(255)", "nationality": "VARCHAR(255)",
            "aadhaar_number": "VARCHAR(255)", "gender": "VARCHAR(50)",
        }
        for field, kind in common.items():
            self.connection.execute(text(f"ALTER TABLE users ADD COLUMN {field} {kind}"))
        legacy_columns = {
            "first_name": "VARCHAR(255)", "middle_name": "VARCHAR(255)", "last_name": "VARCHAR(255)",
            "email": "VARCHAR(255)", "mobile_number": "VARCHAR(20)", "role": "VARCHAR(100)",
            **common, **{name: kind.split(" REFERENCES")[0] for name, kind in PROFILE_COLUMNS.items()},
        }
        legacy_columns["date_of_birth"] = "DATE"
        for field, kind in legacy_columns.items():
            self.connection.execute(text(f"ALTER TABLE staff ADD COLUMN {field} {kind}"))
        self.connection.execute(text("""
            INSERT INTO staff (id, school_id, first_name, email, mobile_number, role, date_of_birth, designation)
            VALUES (3, 1, 'Teacher', 'teacher@example.com', '9876543210', 'Teacher', '1990-01-01', 'Teacher')
        """))
        if partial:
            self.connection.execute(text("""
                ALTER TABLE staff ADD COLUMN user_id INTEGER UNIQUE REFERENCES users(id);
                INSERT INTO staff (id, school_id, user_id, first_name, email, mobile_number, role)
                VALUES (4, 1, 7, 'Test', 'test@example.com', '123', 'ADMIN');
            """))
        migrate_staff_accounts(self.connection)
        migrate_staff_accounts(self.connection)
        if partial:
            self.assertEqual(self.connection.execute(text(
                "SELECT login_user_id FROM staff WHERE id=4"
            )).scalar(), 7)
            self.assertEqual(self.connection.execute(text(
                "SELECT password FROM login_user WHERE id=7"
            )).scalar(), "existing-hash")
        result = self.connection.execute(text("""
            SELECT s.id, s.designation, l.role, l.password, l.last_name
            FROM staff s JOIN login_user l ON l.id=s.login_user_id
            WHERE s.id=3
        """)).one()
        self.assertEqual(tuple(result[:3]), (3, "Teacher", "Teacher"))
        self.assertTrue(verify_password("9876543210", result.password))
        self.assertIsNone(result.last_name)
        self.assertIn("designation", {col["name"] for col in inspect(self.connection).get_columns("staff")})
        self.assertEqual(self.connection.execute(text("SELECT count(*) FROM school_mapping")).scalar(), 0)

    def test_leftover_staff_columns_removed_after_link_was_added(self):
        from database.staff_account_table import remove_migrated_staff_columns
        migrate_legacy_users(self.connection)
        self.connection.execute(text("""
            ALTER TABLE staff ADD COLUMN login_user_id INTEGER REFERENCES login_user(id);
            ALTER TABLE staff ADD COLUMN first_name VARCHAR(255) NOT NULL DEFAULT 'Test';
            ALTER TABLE staff ADD COLUMN mobile_number VARCHAR(20) NOT NULL DEFAULT '123';
            ALTER TABLE staff ADD COLUMN joining_date DATE DEFAULT '2026-04-01';
            ALTER TABLE staff ADD COLUMN salary NUMERIC(12,2) DEFAULT 25000;
            INSERT INTO staff (id, login_user_id) VALUES (1, 7);
        """))
        columns = {col["name"] for col in inspect(self.connection).get_columns("staff")}
        remove_migrated_staff_columns(self.connection, columns)
        self.assertEqual(
            {col["name"] for col in inspect(self.connection).get_columns("staff")},
            {"id", "login_user_id", "joining_date", "salary"},
        )
        self.assertEqual(self.connection.execute(text(
            "SELECT joining_date::text FROM staff WHERE id=1"
        )).scalar(), "2026-04-01")
        self.assertEqual(self.connection.execute(text(
            "SELECT salary FROM staff WHERE id=1"
        )).scalar(), 25000)
        self.assertEqual(self.connection.execute(text("SELECT first_name FROM login_user WHERE id=7")).scalar(), "Test")

    def test_leftover_staff_columns_preserved_on_conflict(self):
        from database.staff_account_table import remove_migrated_staff_columns
        from sqlalchemy.exc import SQLAlchemyError
        migrate_legacy_users(self.connection)
        self.connection.execute(text("""
            ALTER TABLE staff ADD COLUMN login_user_id INTEGER REFERENCES login_user(id);
            ALTER TABLE staff ADD COLUMN first_name VARCHAR(255);
            INSERT INTO staff (id, login_user_id, first_name) VALUES (1, 7, 'Different');
        """))
        columns = {col["name"] for col in inspect(self.connection).get_columns("staff")}
        with self.assertRaisesRegex(SQLAlchemyError, "values differ"):
            remove_migrated_staff_columns(self.connection, columns)
        self.assertIn("first_name", {col["name"] for col in inspect(self.connection).get_columns("staff")})

    def test_permissions_migrate_to_login_account_and_can_repeat(self):
        migrate_legacy_users(self.connection)
        self.connection.execute(text("""
            ALTER TABLE staff ADD COLUMN login_user_id INTEGER REFERENCES login_user(id);
            INSERT INTO staff (id, login_user_id) VALUES (3, 7);
            CREATE TABLE staff_permission (
                id SERIAL PRIMARY KEY, staff_id INTEGER NOT NULL REFERENCES staff(id),
                staff_module_id INTEGER NOT NULL, is_enabled BOOLEAN NOT NULL,
                UNIQUE (staff_id, staff_module_id)
            );
            INSERT INTO staff_permission (staff_id, staff_module_id, is_enabled)
            VALUES (3, 10, false);
        """))
        migrate_user_addresses(self.connection)
        migrate_user_addresses(self.connection)
        row = self.connection.execute(text(
            "SELECT login_user_id, staff_module_id, is_enabled FROM staff_permission"
        )).one()
        self.assertEqual(tuple(row), (7, 10, False))
        self.assertNotIn("staff_id", {c["name"] for c in inspect(self.connection).get_columns("staff_permission")})

    def test_conflicting_address_owners_roll_back_without_losing_data(self):
        migrate_legacy_users(self.connection)
        self.connection.execute(text("""
            ALTER TABLE users DROP COLUMN city;
            INSERT INTO login_user (id, first_name, last_name, email, mobile, password, role)
            VALUES (8, 'Other', 'User', 'other@example.com', '456', 'hash', 'ADMIN');
            CREATE TABLE staff_address (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id),
                login_user_id INTEGER REFERENCES login_user(id),
                line_1 VARCHAR(500), line_2 VARCHAR(500), city VARCHAR(255),
                country VARCHAR(255), state VARCHAR(255), pin_code VARCHAR(20)
            );
            INSERT INTO staff_address (user_id, login_user_id, city) VALUES (7, 8, 'Delhi');
        """))
        from sqlalchemy.exc import SQLAlchemyError
        with self.assertRaisesRegex(SQLAlchemyError, "conflicting account owner"):
            with self.connection.begin_nested():
                migrate_user_addresses(self.connection)
        row = self.connection.execute(text(
            "SELECT user_id, login_user_id, city FROM staff_address"
        )).one()
        self.assertEqual(tuple(row), (7, 8, "Delhi"))

"""PostgreSQL regression coverage for restoring account and staff storage."""
import os
import unittest
import uuid

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from database.staff_account_table import migrate_user_accounts, migrate_staff_profiles
from database.user_address_table import migrate_user_addresses


@unittest.skipUnless(os.environ.get('LOGIN_MIGRATION_TEST_URL'), 'Requires test PostgreSQL')
class StaffStorageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ['LOGIN_MIGRATION_TEST_URL'])
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        schema = 'staff_storage_' + uuid.uuid4().hex
        self.connection.execute(text(f'CREATE SCHEMA {schema}'))
        self.connection.execute(text(f'SET LOCAL search_path TO {schema}'))
        self.connection.execute(text('''
            CREATE TABLE login_user (
                id SERIAL PRIMARY KEY, first_name VARCHAR(255) NOT NULL,
                middle_name VARCHAR(255), last_name VARCHAR(255), email VARCHAR(255) UNIQUE NOT NULL,
                mobile VARCHAR(20) UNIQUE NOT NULL, password VARCHAR(255) NOT NULL, role VARCHAR(100) NOT NULL
            );
            INSERT INTO login_user VALUES (50, 'Teacher', 'Middle', 'Last', 't@example.com', '123', 'existing-hash', 'Teacher');
            CREATE TABLE users (
                id SERIAL PRIMARY KEY, login_user_id INTEGER NOT NULL UNIQUE REFERENCES login_user(id),
                status VARCHAR(20) NOT NULL, designation VARCHAR(255), date_of_birth VARCHAR(50)
            );
            INSERT INTO users VALUES (7, 50, 'ACTIVE', 'Teacher', '1990-01-01');
            SELECT setval(pg_get_serial_sequence('users','id'), 7);
            CREATE TABLE caste_categories (id INTEGER PRIMARY KEY);
            CREATE TABLE staff (id SERIAL PRIMARY KEY, school_id INTEGER NOT NULL,
                                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id));
            INSERT INTO staff VALUES (3, 1, 7);
            CREATE TABLE school_mapping (id SERIAL PRIMARY KEY, school_id INTEGER,
                user_id INTEGER REFERENCES users(id), status VARCHAR(20));
            INSERT INTO school_mapping (school_id,user_id,status) VALUES (1,7,'active');
            CREATE TABLE staff_address (id SERIAL PRIMARY KEY,
                login_user_id INTEGER NOT NULL UNIQUE REFERENCES login_user(id),
                line_1 VARCHAR(500), line_2 VARCHAR(500), city VARCHAR(255),
                country VARCHAR(255), state VARCHAR(255), pin_code VARCHAR(20));
            INSERT INTO staff_address (login_user_id,city) VALUES (50,'Delhi');
            CREATE TABLE staff_permission (id SERIAL PRIMARY KEY,
                login_user_id INTEGER NOT NULL REFERENCES login_user(id), staff_module_id INTEGER NOT NULL,
                is_enabled BOOLEAN NOT NULL, UNIQUE(login_user_id,staff_module_id));
            INSERT INTO staff_permission (login_user_id,staff_module_id,is_enabled) VALUES (50,2,false);
        '''))

    def tearDown(self):
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def migrate(self):
        migrate_user_accounts(self.connection)
        migrate_staff_profiles(self.connection)
        migrate_user_addresses(self.connection)

    def test_split_layout_preserves_ids_hashes_and_ownership_and_repeats(self):
        self.migrate()
        self.migrate()
        row = self.connection.execute(text('''
            SELECT u.id,l.first_name,l.password,s.designation,s.date_of_birth::text,a.city,p.is_enabled
            FROM users u JOIN login_user l ON l.id=u.login_user_id JOIN staff s ON s.login_user_id=l.id
            JOIN staff_address a ON a.login_user_id=l.id JOIN staff_permission p ON p.login_user_id=l.id
        ''')).one()
        self.assertEqual(tuple(row), (7,'Teacher','existing-hash','Teacher','1990-01-01','Delhi',False))
        self.assertNotIn('user_id', {c['name'] for c in inspect(self.connection).get_columns('staff')})
        for table in ('staff_address', 'staff_permission'):
            self.assertNotIn('user_id', {c['name'] for c in inspect(self.connection).get_columns(table)})
        self.connection.execute(text('''
            INSERT INTO login_user (first_name,email,mobile,password,role)
            VALUES ('New','new@example.com','456','new-hash','Teacher')
        '''))
        self.assertEqual(self.connection.execute(text('SELECT count(*) FROM login_user')).scalar(), 2)

    def test_partial_legacy_staff_rows_migrate_without_moving_profile_fields(self):
        self.connection.execute(text('''
            ALTER TABLE staff ALTER COLUMN user_id DROP NOT NULL;
            ALTER TABLE staff ADD COLUMN first_name VARCHAR(255);
            ALTER TABLE staff ADD COLUMN email VARCHAR(255);
            ALTER TABLE staff ADD COLUMN mobile_number VARCHAR(20);
            ALTER TABLE staff ADD COLUMN role VARCHAR(100);
            ALTER TABLE staff ADD COLUMN designation VARCHAR(255);
            UPDATE staff SET designation='Teacher';
            INSERT INTO staff (id,school_id,first_name,email,mobile_number,role,designation)
            VALUES (4,1,'New','new@example.com','456','Teacher','Principal');
        '''))
        self.migrate()
        self.migrate()
        row = self.connection.execute(text('''
            SELECT s.designation,l.first_name FROM staff s
            JOIN login_user l ON l.id=s.login_user_id
            WHERE s.id=4
        ''')).one()
        self.assertEqual(tuple(row), ('Principal', 'New'))
        self.assertEqual(self.connection.execute(text('SELECT count(*) FROM users')).scalar(), 1)
        self.assertEqual(self.connection.execute(text('SELECT count(*) FROM school_mapping')).scalar(), 1)
        self.assertNotIn('first_name', {c['name'] for c in inspect(self.connection).get_columns('staff')})

    def test_conflicting_identity_is_preserved_without_blocking_startup(self):
        self.connection.execute(text("""
            ALTER TABLE users ADD COLUMN email VARCHAR(255) NOT NULL DEFAULT 'other@example.com';
            ALTER TABLE users ADD COLUMN password VARCHAR(255) NOT NULL DEFAULT 'old-hash';
        """))
        self.migrate()
        self.migrate()
        row = self.connection.execute(text("""
            SELECT u.legacy_account_email, u.legacy_account_password, l.email, l.password
            FROM users u JOIN login_user l ON l.id=u.login_user_id WHERE u.id=7
        """)).one()
        self.assertEqual(tuple(row), ('other@example.com', 'old-hash', 't@example.com', 'existing-hash'))
        self.assertNotIn('email', {c['name'] for c in inspect(self.connection).get_columns('users')})
        account_id = self.connection.execute(text("""
            INSERT INTO login_user (first_name,email,mobile,password,role)
            VALUES ('New','new@example.com','456','new-hash','Teacher') RETURNING id
        """)).scalar_one()
        self.connection.execute(text("""
            INSERT INTO users (login_user_id,status) VALUES (:account,'ACTIVE')
        """), {'account': account_id})
        self.assertIsNone(self.connection.execute(text("""
            SELECT legacy_account_email FROM users WHERE login_user_id=:account
        """), {'account': account_id}).scalar())

    def test_direct_user_accounts_move_to_login_user_preserving_hashes(self):
        self.connection.execute(text("""
            ALTER TABLE users ALTER COLUMN login_user_id DROP NOT NULL;
            ALTER TABLE users ADD COLUMN first_name VARCHAR(255);
            ALTER TABLE users ADD COLUMN middle_name VARCHAR(255);
            ALTER TABLE users ADD COLUMN last_name VARCHAR(255);
            ALTER TABLE users ADD COLUMN email VARCHAR(255);
            ALTER TABLE users ADD COLUMN mobile VARCHAR(20);
            ALTER TABLE users ADD COLUMN password VARCHAR(255);
            ALTER TABLE users ADD COLUMN role VARCHAR(100);
            INSERT INTO users (id,first_name,email,mobile,password,role,status)
            VALUES (8,'New','new@example.com','456','preserved-hash','Teacher','ACTIVE');
        """))
        self.migrate()
        self.migrate()
        row = self.connection.execute(text("""
            SELECT u.id,l.first_name,l.password FROM users u
            JOIN login_user l ON l.id=u.login_user_id WHERE u.id=8
        """)).one()
        self.assertEqual(tuple(row), (8, 'New', 'preserved-hash'))
        self.assertNotIn('email', {c['name'] for c in inspect(self.connection).get_columns('users')})

    def test_legacy_owners_resolve_direct_accounts_without_creating_users(self):
        self.connection.execute(text('''
            ALTER TABLE staff_address DROP COLUMN login_user_id;
            ALTER TABLE staff_address ADD COLUMN user_id INTEGER REFERENCES users(id);
            UPDATE staff_address SET user_id=7;
            ALTER TABLE staff_permission DROP COLUMN login_user_id;
            ALTER TABLE staff_permission ADD COLUMN staff_id INTEGER REFERENCES staff(id);
            UPDATE staff_permission SET staff_id=3;
        '''))
        self.migrate()
        self.migrate()
        self.assertEqual(self.connection.execute(text('SELECT login_user_id FROM staff')).scalar(), 50)
        self.assertEqual(self.connection.execute(text('SELECT login_user_id FROM staff_address')).scalar(), 50)
        self.assertEqual(self.connection.execute(text('SELECT login_user_id FROM staff_permission')).scalar(), 50)
        self.assertEqual(self.connection.execute(text('SELECT count(*) FROM users')).scalar(), 1)

    def test_direct_staff_migration_does_not_require_users_table(self):
        self.connection.execute(text("""
            ALTER TABLE staff DROP COLUMN user_id;
            ALTER TABLE staff ADD COLUMN login_user_id INTEGER REFERENCES login_user(id);
            UPDATE staff SET login_user_id=50;
            DROP TABLE users CASCADE;
        """))
        migrate_staff_profiles(self.connection)
        migrate_staff_profiles(self.connection)
        self.assertFalse(inspect(self.connection).has_table('users'))
        self.assertEqual(self.connection.execute(text('SELECT login_user_id FROM staff')).scalar(), 50)
        self.assertEqual(self.connection.execute(text('SELECT password FROM login_user WHERE id=50')).scalar(), 'existing-hash')

    def test_already_migrated_staff_removes_leftover_user_id(self):
        self.migrate()
        self.connection.execute(text("""
            ALTER TABLE staff ADD COLUMN user_id INTEGER REFERENCES users(id);
            UPDATE staff SET user_id=7;
        """))
        self.migrate()
        self.assertNotIn('user_id', {c['name'] for c in inspect(self.connection).get_columns('staff')})
        self.assertEqual(self.connection.execute(text('SELECT login_user_id FROM staff')).scalar(), 50)

    def test_status_moves_to_login_account_and_preserves_disabled_state(self):
        from database.staff_account_table import migrate_account_status
        self.connection.execute(text("ALTER TABLE staff ADD COLUMN status VARCHAR(20); UPDATE staff SET status='Inactive'"))
        self.migrate()
        migrate_account_status(self.connection)
        migrate_account_status(self.connection)
        self.assertEqual(self.connection.execute(text('SELECT status FROM login_user WHERE id=50')).scalar(), 'INACTIVE')
        for table in ('users', 'staff'):
            self.assertNotIn('status', {c['name'] for c in inspect(self.connection).get_columns(table)})
        row = self.connection.execute(text("""
            INSERT INTO login_user (first_name,email,mobile,password,role)
            VALUES ('New','new@example.com','456','hash','Teacher') RETURNING status
        """)).scalar_one()
        self.assertEqual(row, 'ACTIVE')

    def test_pending_status_remains_pending_on_shared_account(self):
        from database.staff_account_table import migrate_account_status
        self.connection.execute(text("UPDATE users SET status='PENDING'"))
        self.migrate()
        migrate_account_status(self.connection)
        self.assertEqual(self.connection.execute(text('SELECT status FROM login_user WHERE id=50')).scalar(), 'PENDING')

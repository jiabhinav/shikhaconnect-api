"""PostgreSQL migration checks using an isolated, rolled-back schema."""
import os
import unittest
import uuid

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from database.school_mapping_table import migrate_school_mapping_accounts


@unittest.skipUnless(os.environ.get('LOGIN_MIGRATION_TEST_URL'), 'Requires test PostgreSQL')
class SchoolMappingMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ['LOGIN_MIGRATION_TEST_URL'])
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        schema = 'mapping_test_' + uuid.uuid4().hex
        self.connection.execute(text(f'CREATE SCHEMA {schema}'))
        self.connection.execute(text(f'SET LOCAL search_path TO {schema}'))
        self.connection.execute(text('''
            CREATE TABLE login_user (id INTEGER PRIMARY KEY);
            INSERT INTO login_user VALUES (1), (2), (80);
            CREATE TABLE users (id INTEGER PRIMARY KEY, login_user_id INTEGER UNIQUE REFERENCES login_user(id));
            INSERT INTO users VALUES (1, 2), (2, 1);
            CREATE TABLE school_mapping (
                id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status VARCHAR(20), CONSTRAINT uq_school_mapping_user UNIQUE(school_id, user_id));
            INSERT INTO school_mapping VALUES (10, 5, 1, 'active'), (20, 5, 2, 'deactive');
        '''))

    def tearDown(self):
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def test_swapped_ids_preserved_and_migration_repeatable(self):
        migrate_school_mapping_accounts(self.connection)
        migrate_school_mapping_accounts(self.connection)
        rows = self.connection.execute(text('SELECT * FROM school_mapping ORDER BY id')).all()
        self.assertEqual([tuple(row) for row in rows], [(10, 5, 2, 'active'), (20, 5, 1, 'deactive')])
        fk = inspect(self.connection).get_foreign_keys('school_mapping')[0]
        self.assertEqual(fk['referred_table'], 'login_user')
        self.connection.execute(text("INSERT INTO school_mapping VALUES (30, 5, 80, 'active')"))
        self.connection.execute(text('DELETE FROM login_user WHERE id=80'))
        self.assertEqual(self.connection.execute(text('SELECT count(*) FROM school_mapping')).scalar(), 2)

    def test_missing_account_fails_without_changing_assignments(self):
        self.connection.execute(text('UPDATE users SET login_user_id=NULL WHERE id=1'))
        with self.assertRaises(SQLAlchemyError):
            with self.connection.begin_nested():
                migrate_school_mapping_accounts(self.connection)
        self.assertEqual(self.connection.execute(text('SELECT user_id FROM school_mapping WHERE id=10')).scalar(), 1)
        self.assertEqual(inspect(self.connection).get_foreign_keys('school_mapping')[0]['referred_table'], 'users')

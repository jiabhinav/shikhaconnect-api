import os
import unittest
import uuid
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from database.table_init import register_models
from database.session_catalogs import migrate_session_catalogs, CATALOG_TABLES


@unittest.skipUnless(os.environ.get('LOGIN_MIGRATION_TEST_URL'), 'Requires test PostgreSQL')
class SessionCatalogMigrationTests(unittest.TestCase):
    def setUp(self):
        register_models()
        self.engine = create_engine(os.environ['LOGIN_MIGRATION_TEST_URL'])
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        schema = 'session_catalog_' + uuid.uuid4().hex
        self.connection.execute(text(f'CREATE SCHEMA {schema}'))
        self.connection.execute(text(f'SET LOCAL search_path TO {schema}'))
        self.connection.execute(text('''
            CREATE TABLE schools (id INTEGER PRIMARY KEY);
            INSERT INTO schools VALUES (1), (2);
            CREATE TABLE sessions (id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(id),
                                   start_date DATE, end_date DATE);
            INSERT INTO sessions VALUES
                (1,1,'2025-01-01','2025-12-31'), (2,1,'2026-01-01','2026-12-31'),
                (3,1,'2026-04-01','2027-03-31'), (4,1,'2027-04-01','2028-03-31');
        '''))
        from database.database import Base
        for name in CATALOG_TABLES:
            extra = ', code VARCHAR(50)' if name == 'subjects' else ''
            self.connection.execute(text(f'''
                CREATE TABLE {name} (id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(id),
                                     name VARCHAR(100) NOT NULL {extra});
                INSERT INTO {name} (id, school_id, name) VALUES (10,1,'General'), (20,2,'No session');
            '''))
            for index in Base.metadata.tables[name].indexes:
                if index.unique:
                    field = 'code' if index.name.endswith('_code') else 'name'
                    self.connection.execute(text(f'CREATE UNIQUE INDEX {index.name} ON {name} (school_id, lower(trim({field})))'))

    def tearDown(self):
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def test_backfill_preserves_rows_and_scopes_uniqueness(self):
        with patch('database.session_catalogs.today', return_value=date(2026, 9, 23)):
            migrate_session_catalogs(self.connection)
        # Do not silently move records when the next year begins.
        with patch('database.session_catalogs.today', return_value=date(2027, 9, 23)):
            migrate_session_catalogs(self.connection)
        for name in CATALOG_TABLES:
            with self.subTest(table=name):
                rows = self.connection.execute(text(f'SELECT id,session_id FROM {name} ORDER BY id')).all()
                self.assertEqual([tuple(row) for row in rows], [(10,3), (20,None)])
                self.assertTrue(any(fk['referred_table'] == 'sessions' for fk in inspect(self.connection).get_foreign_keys(name)))
                self.connection.execute(text(f"INSERT INTO {name} (id,school_id,session_id,name) VALUES (30,1,2,'General')"))
                with self.assertRaises(IntegrityError):
                    with self.connection.begin_nested():
                        self.connection.execute(text(f"INSERT INTO {name} (id,school_id,session_id,name) VALUES (40,1,3,' general ')"))
                with self.assertRaises(IntegrityError):
                    with self.connection.begin_nested():
                        self.connection.execute(text(f"INSERT INTO {name} (id,school_id,session_id,name) VALUES (50,1,999,'Missing')"))

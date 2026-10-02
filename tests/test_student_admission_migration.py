"""Run with LOGIN_MIGRATION_TEST_URL pointing to a test PostgreSQL database."""
import os
import unittest
import uuid
from sqlalchemy import create_engine, inspect, text
from database.student_table import migrate_student_admission_storage


@unittest.skipUnless(os.environ.get('LOGIN_MIGRATION_TEST_URL'), 'Requires test PostgreSQL')
class StudentAdmissionMigrationTests(unittest.TestCase):
    def test_preserves_existing_data_and_is_repeatable(self):
        engine = create_engine(os.environ['LOGIN_MIGRATION_TEST_URL'])
        self.addCleanup(engine.dispose)
        c = engine.connect()
        self.addCleanup(c.close)
        transaction = c.begin()
        self.addCleanup(transaction.rollback)
        schema = 'student_migration_' + uuid.uuid4().hex
        c.execute(text(f'CREATE SCHEMA {schema}'))
        c.execute(text(f'SET LOCAL search_path TO {schema}'))
        c.execute(text('CREATE TABLE students (id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL)'))
        c.execute(text("CREATE TABLE student_login (id INTEGER PRIMARY KEY, student_id INTEGER UNIQUE REFERENCES students(id), school_id INTEGER, admission_number VARCHAR(150), admission_sequence INTEGER, status VARCHAR(20), password TEXT, CONSTRAINT uq_student_admission UNIQUE(school_id, admission_number), CONSTRAINT uq_student_admission_sequence UNIQUE(school_id, admission_sequence), CONSTRAINT ck_student_login_status CHECK(status IN ('active', 'inactive')))"))
        c.execute(text('INSERT INTO students VALUES (1, 313), (2, 313)'))
        c.execute(text("INSERT INTO student_login VALUES (7, 1, 313, 'ADM-100-S', 100, 'inactive', 'preserved-hash')"))
        migrate_student_admission_storage(c)
        migrate_student_admission_storage(c)
        assert c.execute(text('SELECT login_id, admission_number, admission_sequence, status FROM students WHERE id=1')).one() == (7, 'ADM-100-S', 100, 'inactive')
        assert c.execute(text('SELECT login_id, status FROM students WHERE id=2')).one() == (None, 'active')
        assert c.execute(text('SELECT password FROM student_login WHERE id=7')).scalar() == 'preserved-hash'
        assert not {'student_id', 'admission_number', 'admission_sequence', 'status'} & {x['name'] for x in inspect(c).get_columns('student_login')}

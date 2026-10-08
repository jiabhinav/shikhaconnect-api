import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from database.teacher_table import migrate_teacher_employee_codes


class TeacherEmployeeMigrationTests(unittest.TestCase):
    def test_existing_teachers_preserved_and_codes_unique_per_school(self):
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE teachers (id INTEGER PRIMARY KEY, school_id INTEGER)'))
                connection.execute(text('INSERT INTO teachers VALUES (1, 1), (2, 1), (3, 2)'))
                migrate_teacher_employee_codes(connection)
                migrate_teacher_employee_codes(connection)
                columns = {column['name'] for column in inspect(connection).get_columns('teachers')}
                self.assertTrue({'employee_code', 'employee_sequence'} <= columns)
                self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM teachers')).scalar(), 3)
                connection.execute(text("UPDATE teachers SET employee_code='EMP1', employee_sequence=1 WHERE id IN (1, 3)"))
                for assignment in ("employee_code='EMP1'", 'employee_sequence=1'):
                    with self.assertRaises(IntegrityError):
                        with connection.begin_nested():
                            connection.execute(text(f'UPDATE teachers SET {assignment} WHERE id=2'))
        finally:
            engine.dispose()

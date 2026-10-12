import unittest
from sqlalchemy import create_engine, inspect, text
from database.student_table import migrate_student_form_fields
from models.student import Student


class StudentProfileImageTests(unittest.TestCase):
    def test_missing_column_created_and_existing_values_preserved(self):
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE students (id INTEGER PRIMARY KEY)'))
                connection.execute(text('INSERT INTO students (id) VALUES (1)'))
                migrate_student_form_fields(connection)
                column = next(c for c in inspect(connection).get_columns('students') if c['name'] == 'profile_img')
                self.assertTrue(column['nullable'])
                self.assertIsNone(connection.execute(text('SELECT profile_img FROM students WHERE id=1')).scalar())
                connection.execute(text("UPDATE students SET profile_img='students/1/photo.jpg' WHERE id=1"))
                migrate_student_form_fields(connection)
                self.assertEqual(connection.execute(text('SELECT profile_img FROM students WHERE id=1')).scalar(), 'students/1/photo.jpg')
                self.assertEqual(Student.__table__.c.profile_img.type.length, 500)
        finally:
            engine.dispose()

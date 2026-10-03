from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine, text

from database.school_status_table import ensure_school_status
from dependencies.auth import get_current_user
from models.user import UserRole
from test_school_creation import SchoolCreationTests


class SchoolStatusTests(unittest.TestCase):
    setUp = SchoolCreationTests.setUp
    tearDown = SchoolCreationTests.tearDown

    def create_school(self, email):
        payload = self.payload | {"school_info": self.payload["school_info"] | {"primary_email": email}}
        response = self.client.post('/super-admin/create_school', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()['data']['status'], True)
        return response.json()['data']['id']

    def test_status_update_and_filters(self):
        active_id = self.create_school('active@example.com')
        inactive_id = self.create_school('inactive@example.com')
        url = f'/super-admin/school/status?school_id={inactive_id}'
        for _ in range(2):
            response = self.client.patch(url, json={'status': False})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data'], {'id': inactive_id, 'status': False})
        for value, expected in [('true', [active_id]), ('false', [inactive_id])]:
            response = self.client.get('/super-admin/school', params={'status': value})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([s['id'] for s in response.json()['data']], expected)
            self.assertTrue(all(s['status'] == (value == 'true') for s in response.json()['data']))
        self.assertEqual(len(self.client.get('/super-admin/school').json()['data']), 2)
        self.assertEqual(self.client.get('/super-admin/school?status=invalid').status_code, 422)
        response = self.client.patch(url, json={'status': True})
        self.assertIs(response.json()['data']['status'], True)
        self.assertEqual(self.client.get('/super-admin/school?status=false').json()['data'], [])

    def test_validation_missing_school_and_authorization(self):
        school_id = self.create_school('school@example.com')
        url = f'/super-admin/school/status?school_id={school_id}'
        for body in ({}, {'status': None}, {'status': 'false'}, {'status': 0}):
            self.assertEqual(self.client.patch(url, json=body).status_code, 422)
        self.assertEqual(self.client.patch('/super-admin/school/status?school_id=999', json={'status': False}).status_code, 404)
        self.assertEqual(self.client.patch('/super-admin/school/status', json={'status': False}).status_code, 422)
        self.assertEqual(self.client.patch('/super-admin/school/status?school_id=invalid', json={'status': False}).status_code, 422)
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN, "Teacher"):
            self.client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role=role)
            self.assertEqual(self.client.patch(url, json={'status': False}).status_code, 403)
            self.assertEqual(self.client.get('/super-admin/school?status=false').status_code, 403)


class SchoolStatusMigrationTests(unittest.TestCase):
    def test_existing_and_manual_columns(self):
        for manual_column in (False, True):
            engine = create_engine('sqlite://')
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE schools (id INTEGER PRIMARY KEY' +
                                        (', status BOOLEAN)' if manual_column else ')')))
                connection.execute(text('INSERT INTO schools (id) VALUES (1)'))
                if manual_column:
                    connection.execute(text('INSERT INTO schools (id, status) VALUES (2, false)'))
                ensure_school_status(connection)
                ensure_school_status(connection)
                self.assertEqual(connection.execute(text('SELECT status FROM schools WHERE id=1')).scalar_one(), 1)
                if manual_column:
                    self.assertEqual(connection.execute(text('SELECT status FROM schools WHERE id=2')).scalar_one(), 0)
            engine.dispose()

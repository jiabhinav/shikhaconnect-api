import unittest
from copy import deepcopy
from unittest.mock import patch

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

import test_sessions
from database.table_init import ensure_all_tables
from models.caste_category import CasteCategory
from models.staff import Staff, StaffAddress, StaffPermission
from models.user import UserRole


class StaffTests(unittest.TestCase):
    def setUp(self):
        test_sessions.SessionTests.setUp(self)
        self.url = '/schools/school/1/staff'
        self.db.add_all([
            CasteCategory(id=1, school_id=1, name='General'),
            CasteCategory(id=2, school_id=2, name='General'),
        ])
        self.db.execute(text('CREATE TABLE staff_modules (id INTEGER PRIMARY KEY, name TEXT, status BOOLEAN)'))
        self.db.execute(text("INSERT INTO staff_modules VALUES (1, 'Dashboard', true), (2, 'School', true), (3, 'Inactive', false)"))
        self.db.commit()
        self.payload = {
            'staff_info': {
                'first_name': 'Test', 'date_of_birth': '1990-01-01',
                'designation': 'Teacher', 'mobile_number': '9876543210',
                'email': 'staff@example.com', 'father_name': 'Father', 'mother_name': 'Mother',
                'nationality': 'Indian', 'aadhaar_number': '123456789012',
                'caste_category_id': 1, 'role': 'Teacher', 'gender': 'Female',
                'salary': '25000.50', 'joining_date': '2026-04-01',
            },
            'address': {'line_1': 'Street 1', 'city': 'Delhi', 'country': 'India',
                        'state': 'Delhi', 'pin_code': '110001'},
            'permissions': [{'staff_module_id': 1, 'is_enabled': True}, {'staff_module_id': 2, 'is_enabled': False}],
        }

    tearDown = test_sessions.SessionTests.tearDown

    def test_create_and_fetch_three_sections(self):
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual(item['school_id'], 1)
        self.assertEqual(item['staff_info']['salary'], '25000.50')
        self.assertIsNone(item['staff_info']['last_name'])
        self.assertEqual(item['address']['staff_id'], item['id'])
        self.assertEqual([p['is_enabled'] for p in item['permissions']], [True, False])
        self.assertTrue(all(p['staff_id'] == item['id'] for p in item['permissions']))
        for model, count in ((Staff, 1), (StaffAddress, 1), (StaffPermission, 2)):
            self.assertEqual(self.db.query(model).count(), count)
        self.assertEqual(self.client.get(f"{self.url}/{item['id']}").json()['data'], item)
        self.assertEqual(self.client.get(self.url).json()['data'], [item])
        self.assertEqual(self.client.get(self.url + '?offset=1').json()['data'], [])
        self.assertEqual(self.client.get('/schools/school/2/staff').json()['data'], [])
        self.assertEqual(self.client.get(f"/schools/school/2/staff/{item['id']}").status_code, 404)

    def test_invalid_references_and_duplicate_permissions_do_not_save(self):
        for permissions in ([{'staff_module_id': 999}], [{'staff_module_id': 3}],
                            [{'staff_module_id': 1}, {'staff_module_id': 1}]):
            payload = dict(self.payload, permissions=permissions)
            self.assertEqual(self.client.post(self.url, json=payload).status_code, 422)
        for category_id in (2, 999):
            payload = deepcopy(self.payload)
            payload['staff_info']['caste_category_id'] = category_id
            self.assertEqual(self.client.post(self.url, json=payload).status_code, 404)
        self.assertEqual(self.db.query(Staff).count(), 0)

    def test_validation_and_empty_permissions(self):
        for section, field, value in (
            ('staff_info', 'first_name', ''), ('staff_info', 'email', 'invalid'),
            ('staff_info', 'date_of_birth', 'invalid'), ('staff_info', 'salary', '-1'),
            ('address', 'line_1', ''), ('address', 'pin_code', ''),
        ):
            payload = deepcopy(self.payload)
            payload[section][field] = value
            self.assertEqual(self.client.post(self.url, json=payload).status_code, 422)
        payload = dict(self.payload, permissions=[])
        response = self.client.post(self.url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['data']['permissions'], [])

    def test_failed_save_rolls_back_all_tables(self):
        real_flush = self.db.flush
        def failed_commit():
            real_flush()
            raise IntegrityError('insert', {}, Exception('test failure'))
        with patch.object(self.db, 'commit', side_effect=failed_commit):
            response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 409)
        for model in (Staff, StaffAddress, StaffPermission):
            self.assertEqual(self.db.query(model).count(), 0)

    def test_missing_school_and_access(self):
        self.assertEqual(self.client.post('/schools/school/999/staff', json=self.payload).status_code, 404)
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, json=self.payload).status_code, 403)

    def test_auto_creation_is_repeatable_and_preserves_data(self):
        self.db.close()
        with self.engine.begin() as connection:
            for model in (StaffPermission, StaffAddress, Staff):
                model.__table__.drop(connection)
            ensure_all_tables(connection)
            self.assertTrue({'staff', 'staff_address', 'staff_permission'}.issubset(
                inspect(connection).get_table_names()))
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.db.close()
        with self.engine.begin() as connection:
            ensure_all_tables(connection)
        self.assertEqual(len(self.client.get(self.url).json()['data']), 1)

    def test_legacy_module_column_is_renamed_without_losing_permissions(self):
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        expected = response.json()['data']
        self.db.close()
        with self.engine.begin() as connection:
            connection.execute(text(
                'ALTER TABLE staff_permission RENAME COLUMN staff_module_id TO module_id'
            ))
            ensure_all_tables(connection)
            ensure_all_tables(connection)
            columns = {column['name'] for column in inspect(connection).get_columns('staff_permission')}
            self.assertIn('staff_module_id', columns)
            self.assertNotIn('module_id', columns)
            constraints = inspect(connection).get_unique_constraints('staff_permission')
            self.assertTrue(any(c['column_names'] == ['staff_id', 'staff_module_id'] for c in constraints))
        self.assertEqual(self.client.get(f"{self.url}/{expected['id']}").json()['data'], expected)

import unittest
from copy import deepcopy

from sqlalchemy import text

import test_staff
from database.module_names import get_staff_module_names
from models.school import SchoolPermission


class StaffSchoolPermissionTests(unittest.TestCase):
    def setUp(self):
        test_staff.StaffTests.setUp(self)
        self.db.execute(text('DROP TABLE staff_modules'))
        self.db.execute(text('CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT)'))
        self.db.execute(text("INSERT INTO modules VALUES (1, 'Attendance'), (2, 'Fees'), (3, 'Reports'), (4, 'Library')"))
        self.db.add_all([
            SchoolPermission(school_id=1, module_id=1, is_enabled=True),
            SchoolPermission(school_id=1, module_id=2, is_enabled=True),
            SchoolPermission(school_id=1, module_id=3, is_enabled=False),
            SchoolPermission(school_id=2, module_id=4, is_enabled=True),
        ])
        self.db.commit()
        self.create_url = '/schools/staff?school_id=1'

    tearDown = test_staff.StaffTests.tearDown

    def test_create_read_update_without_staff_modules_table(self):
        response = self.client.post(self.create_url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual([p['name'] for p in item['permissions']], ['Attendance', 'Fees'])
        url = f"{self.url}/{item['id']}"
        self.assertEqual(self.client.get(url).json()['data']['permissions'], item['permissions'])
        self.payload['permissions'] = [{'staff_module_id': 2}]
        response = self.client.put(url, json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['permissions'][0]['name'], 'Fees')
        self.assertEqual(get_staff_module_names(self.db, [1]), {1: 'Attendance'})

    def test_reject_disabled_unassigned_and_missing_modules(self):
        for module_id in (3, 4, 999):
            with self.subTest(module_id=module_id):
                payload = deepcopy(self.payload)
                payload['permissions'] = [{'staff_module_id': module_id}]
                response = self.client.post(self.create_url, json=payload)
                self.assertEqual(response.status_code, 422, response.text)

    def test_reject_globally_inactive_module(self):
        self.db.execute(text('ALTER TABLE modules ADD COLUMN status BOOLEAN DEFAULT true'))
        self.db.execute(text('UPDATE modules SET status=false WHERE id=1'))
        self.db.commit()
        response = self.client.post(self.create_url, json=self.payload)
        self.assertEqual(response.status_code, 422, response.text)

    def test_action_flags_are_stored_and_updated(self):
        from models.staff import StaffPermission
        flags = dict(read=True, delete=False, update=True, create=False)
        self.payload['permissions'][0].update(flags)
        response = self.client.post(self.create_url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        permission = item['permissions'][0]
        self.db.expire_all()
        stored = self.db.get(StaffPermission, permission['id'])
        for key, value in flags.items():
            self.assertEqual(permission[key], value)
            self.assertEqual(getattr(stored, key), value)
            self.assertEqual(item['permissions'][1][key], key == 'read')
        flags = {key: not value for key, value in flags.items()}
        self.payload['permissions'][0].update(flags)
        url = f"{self.url}/{item['id']}"
        response = self.client.put('/schools/staff',
                                   params={'school_id': 1, 'user_id': item['login_user_id']},
                                   json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        updated_permission = response.json()['data']['permissions'][0]
        stored = self.db.get(StaffPermission, updated_permission['id'])
        fetched = self.client.get(url).json()['data']['permissions'][0]
        for key, value in flags.items():
            self.assertEqual(updated_permission[key], value)
            self.assertEqual(getattr(stored, key), value)
            self.assertEqual(fetched[key], value)

    def test_patch_single_permission_flags_preserves_other_values(self):
        response = self.client.post(self.create_url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        params = dict(school_id=1, user_id=item['login_user_id'], staff_module_id=1)
        url = '/schools/staff/permissions'
        expected = dict(read=True, delete=False, update=False, create=False)
        for flag in expected:
            for value in (True, False):
                response = self.client.patch(url, params=params, json={flag: value})
                self.assertEqual(response.status_code, 200, response.text)
                expected[flag] = value
                for key in expected:
                    self.assertEqual(response.json()['data'][key], expected[key])
                fetched = self.client.get(f"{self.url}/{item['id']}").json()['data']
                self.assertEqual(fetched['permissions'][1], item['permissions'][1])
                for key in expected:
                    self.assertEqual(fetched['permissions'][0][key], expected[key])
        for payload in ({}, {'read': None}, {'read': 'true'}, {'is_enabled': False}, {'unknown': True}):
            self.assertEqual(self.client.patch(url, params=params, json=payload).status_code, 422)
        for changes in (dict(school_id=2), dict(user_id=999), dict(staff_module_id=999)):
            self.assertEqual(self.client.patch(url, params={**params, **changes},
                                              json={'read': True}).status_code, 404)
        from dependencies.auth import get_current_user
        del self.client.app.dependency_overrides[get_current_user]
        self.assertIn(self.client.patch(url, params=params, json={'read': True}).status_code, (401, 403))

    def test_action_column_migration_preserves_existing_rows(self):
        from sqlalchemy import create_engine, inspect
        from database.staff_permission_table import add_staff_permission_actions
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE staff_permission (id INTEGER PRIMARY KEY, is_enabled BOOLEAN)'))
                connection.execute(text('INSERT INTO staff_permission VALUES (1, true)'))
                add_staff_permission_actions(connection)
                self.assertTrue(connection.execute(text('SELECT "read" FROM staff_permission')).scalar_one())
                connection.execute(text('UPDATE staff_permission SET "read"=false WHERE id=1'))
                add_staff_permission_actions(connection)
                row = connection.execute(text('SELECT * FROM staff_permission')).mappings().one()
                self.assertEqual(dict(row), dict(id=1, is_enabled=1, read=0, delete=0, update=0, create=0))
                columns = {c['name']: c for c in inspect(connection).get_columns('staff_permission')}
                for name in ('read', 'delete', 'update', 'create'):
                    self.assertFalse(columns[name]['nullable'])
        finally:
            engine.dispose()

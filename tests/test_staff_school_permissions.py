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

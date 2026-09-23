import unittest
from copy import deepcopy
from unittest.mock import patch

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

import test_sessions
from database.table_init import ensure_all_tables
from models.caste_category import CasteCategory
from models.staff import Staff, StaffAddress, StaffPermission
from models.school_mapping import SchoolMapping
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
        self.assertEqual(item['address']['login_user_id'], self.db.get(Staff, item['id']).login_user_id)
        self.assertEqual([p['is_enabled'] for p in item['permissions']], [True, False])
        self.assertTrue(all(p['login_user_id'] == self.db.get(Staff, item['id']).login_user_id for p in item['permissions']))
        for model, count in ((Staff, 1), (StaffAddress, 1), (StaffPermission, 2)):
            self.assertEqual(self.db.query(model).count(), count)
        self.assertEqual(self.client.get(f"{self.url}/{item['id']}").json()['data'], item)
        self.assertEqual(self.client.get(self.url).json()['data'], [item])
        self.assertEqual(self.client.get(self.url + '?offset=1').json()['data'], [])
        self.assertEqual(self.client.get('/schools/school/2/staff').json()['data'], [])
        self.assertEqual(self.client.get(f"/schools/school/2/staff/{item['id']}").status_code, 404)

    def test_list_excludes_admin_accounts_before_pagination(self):
        roles = [role.value for role in UserRole] + list(UserRole.__members__) + [
            'SuperAdmin', 'SubAdmin', 'Teacher', 'Librarian',
        ]
        expected = []
        for index, role in enumerate(roles):
            payload = deepcopy(self.payload)
            payload['staff_info'].update(email=f'list{index}@example.com',
                                         mobile_number=f'987650{index:04}')
            if role == 'Librarian':
                payload['permissions'] = []
                payload['staff_info']['role'] = role
            response = self.client.post(self.url, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            item = response.json()['data']
            self.db.get(Staff, item['id']).login_user.role = role
            self.db.commit()
            if role in ('Teacher', 'Librarian'):
                expected.append(item)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], expected)
        self.assertEqual(self.client.get(self.url + '?limit=1').json()['data'], expected[:1])
        self.assertEqual(self.client.get(self.url + '?offset=1&limit=1').json()['data'], expected[1:])
        self.assertEqual(self.client.get('/schools/school/2/staff').json()['data'], [])

    def test_list_incomplete_stored_profile_and_address(self):
        from models.user import LoginUser
        account = LoginUser(first_name='Legacy', email='legacy@example.com',
                            mobile='9876540000', role='Office Staff')
        staff = Staff(school_id=1, login_user=account)
        self.db.add(staff)
        self.db.commit()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()['data'][0]
        self.assertEqual(item['staff_info']['role'], 'Office Staff')
        self.assertIsNone(item['staff_info']['date_of_birth'])
        self.assertIsNone(item['address'])
        self.assertNotIn('password', item['staff_info'])
        account.address = StaffAddress(city='Delhi')
        account.permissions = [StaffPermission(staff_module_id=1)]
        self.db.commit()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()['data'][0]
        self.assertEqual(item['address']['city'], 'Delhi')
        self.assertIsNone(item['address']['line_1'])
        self.assertEqual(item['permissions'][0]['name'], 'Dashboard')

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
        from models.user import User, LoginUser
        self.assertIsNone(self.db.query(User).filter_by(email="staff@example.com").first())
        self.assertIsNone(self.db.query(LoginUser).filter_by(email="staff@example.com").first())

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
            self.assertTrue(any(c['column_names'] == ['login_user_id', 'staff_module_id'] for c in constraints))
        self.assertEqual(self.client.get(f"{self.url}/{expected['id']}").json()['data'], expected)

    def test_shared_accounts_update_and_delete(self):
        from models.user import LoginUser, User
        from utils.passwords import verify_password
        payload = deepcopy(self.payload)
        payload["staff_info"]["password"] = "staff-secret"
        payload["staff_info"]["middle_name"] = "Middle"
        payload["staff_info"]["last_name"] = "Last"
        response = self.client.post(self.url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        staff_id = response.json()["data"]["id"]
        self.assertNotIn("password", response.json()["data"]["staff_info"])
        staff = self.db.get(Staff, staff_id)
        account_id = staff.login_user_id
        account = self.db.get(LoginUser, account_id)
        self.assertIs(staff.login_user, account)
        self.assertEqual(staff.login_user.mobile, payload["staff_info"]["mobile_number"])
        self.assertEqual(staff.login_user.email, payload["staff_info"]["email"])
        self.assertEqual(account.role, "Teacher")
        self.assertEqual(account.middle_name, "Middle")
        self.assertEqual(account.last_name, "Last")
        stored = self.db.execute(text(
            "SELECT first_name, middle_name, last_name, email, mobile, password FROM login_user WHERE id=:id"
        ), {"id": account_id}).one()
        self.assertEqual(tuple(stored[:5]), ("Test", "Middle", "Last", "staff@example.com", "9876543210"))
        self.assertNotEqual(stored.password, "staff-secret")
        self.assertEqual(str(self.db.execute(text(
            "SELECT salary FROM staff WHERE id=:id"
        ), {"id": staff_id}).scalar()), "25000.5")
        self.assertTrue(verify_password("staff-secret", account.password))
        self.assertEqual(staff.designation, "Teacher")
        self.assertNotIn("first_name", Staff.__table__.columns)
        self.assertIn("designation", Staff.__table__.columns)
        self.assertNotIn("user_id", Staff.__table__.columns)
        self.assertIsNone(self.db.query(User).filter_by(login_user_id=account_id).first())
        self.assertEqual(self.db.query(LoginUser).filter_by(id=account_id).count(), 1)
        self.assertEqual(self.db.query(SchoolMapping).count(), 0)

        payload["staff_info"].pop("password")
        payload["staff_info"]["first_name"] = "Updated"
        payload["staff_info"]["qualification"] = "B.Ed."
        response = self.client.put(f"{self.url}/{staff_id}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(LoginUser, account_id).first_name, "Updated")
        self.assertTrue(verify_password("staff-secret", self.db.get(LoginUser, account_id).password))
        self.assertEqual(self.db.get(Staff, staff_id).qualification, "B.Ed.")
        self.assertEqual(self.client.post(self.url, json=payload).status_code, 409)

        response = self.client.delete(f"{self.url}/{staff_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(self.db.query(User).count(), 1)
        self.assertIsNone(self.db.get(LoginUser, account_id))
        self.assertIsNone(self.db.get(Staff, staff_id))

    def test_staff_can_login_with_mobile_default_password(self):
        from routers.auth import login
        from models.school import School
        from schemas.user import UserLogin
        import json
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        school = self.db.get(School, 1)
        school.primary_email = "school@example.com"
        school.primary_number = "1234567890"
        from models.school import SchoolPermission
        self.db.add(SchoolPermission(school_id=1, module_id=999, is_enabled=True))
        self.db.commit()
        mobile = self.payload["staff_info"]["mobile_number"]
        result = login(UserLogin(mobile=mobile, password=mobile), self.db)
        data = json.loads(result.body)
        self.assertEqual(data["data"]["role"], "Teacher")
        self.assertIsNone(data["data"]["last_name"])
        self.assertEqual(data["data"]["schools"][0]["id"], 1)
        from schemas.user import UserLoginResponse
        UserLoginResponse.model_validate(data)
        permissions = data["data"]["schools"][0]["permissions"]
        self.assertEqual([p["staff_module_id"] for p in permissions], [1, 2])
        self.assertEqual([p["name"] for p in permissions], ["Dashboard", "School"])
        self.assertEqual([p["is_enabled"] for p in permissions], [True, False])
        self.assertTrue(all("module_id" not in p for p in permissions))
        self.assertTrue(all(p["login_user_id"] == response.json()["data"]["login_user_id"]
                            for p in permissions))
        from dependencies.auth import get_current_user
        from fastapi.security import HTTPAuthorizationCredentials
        principal = get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=data["token"]), self.db)
        self.assertIsInstance(principal, Staff)
        self.assertEqual(principal.id, response.json()["data"]["id"])

    def test_create_does_not_write_users_or_school_mapping(self):
        from sqlalchemy import event
        from models.user import User, LoginUser
        user_count = self.db.query(User).count()
        statements = []
        def record(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("INSERT"):
                statements.append(statement.lower())
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            response = self.client.post(self.url, json=self.payload)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(response.status_code, 201, response.text)
        inserted_tables = {sql.split()[2].strip('"') for sql in statements}
        self.assertEqual(inserted_tables, {
            "login_user", "staff", "staff_address", "staff_permission",
        })
        self.assertEqual(self.db.query(User).count(), user_count)
        staff = self.db.get(Staff, response.json()["data"]["id"])
        stored_id = self.db.execute(text(
            "SELECT login_user_id FROM staff WHERE id=:id"
        ), {"id": staff.id}).scalar_one()
        self.assertIsNotNone(stored_id)
        self.assertEqual(response.json()["data"]["login_user_id"], stored_id)
        self.assertEqual(stored_id, staff.login_user.id)
        self.assertEqual(staff.address.login_user_id, staff.login_user_id)
        self.assertTrue(all(p.login_user_id == staff.login_user_id for p in staff.permissions))
        self.assertIsNotNone(self.db.get(LoginUser, staff.login_user_id))

    def test_staff_role_enum_accepts_dropdown_values_and_rejects_other_roles(self):
        from models.staff import StaffRole
        for index, role in enumerate(StaffRole):
            payload = deepcopy(self.payload)
            payload['staff_info'].update(role=role.value, email=f'role{index}@example.com',
                                         mobile_number=f'98765432{index:02}')
            response = self.client.post(self.url, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()['data']['staff_info']['role'], role.value)
            account = self.db.get(Staff, response.json()['data']['id']).login_user
            self.assertEqual(account.role, role.value)
        staff_id = response.json()['data']['id']
        for role in ('Select Roles', 'Admin', 'Sub Admin', 'Super Admin', 'Unknown', 'Principle'):
            payload['staff_info']['role'] = role
            self.assertEqual(self.client.post(self.url, json=payload).status_code, 422)
            self.assertEqual(self.client.put(f'{self.url}/{staff_id}', json=payload).status_code, 422)

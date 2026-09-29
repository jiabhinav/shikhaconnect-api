import unittest

import test_sessions
from dependencies.auth import get_current_user, account_token
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from models.staff import Staff
from models.user import LoginUser, UserStatus, UserRole


class StaffStatusTests(unittest.TestCase):
    def setUp(self):
        test_sessions.SessionTests.setUp(self)
        self.user.login_user_id = 1
        self.staff = Staff(school_id=1, login_user=LoginUser(
            first_name='Teacher', email='teacher@example.com', mobile='9876543210',
            role='Teacher', password='hash'))
        self.db.add(self.staff)
        self.db.commit()
        self.url = '/schools/staff/status'
        self.params = {'school_id': 1, 'user_id': self.staff.login_user_id}

    tearDown = test_sessions.SessionTests.tearDown

    def test_list_deactive_filters_before_pagination(self):
        expected = []
        for index, (school_id, role, status) in enumerate([
            (1, 'Teacher', UserStatus.PENDING),
            (1, 'Admin', UserStatus.INACTIVE),
            (2, 'Teacher', UserStatus.INACTIVE),
            (1, 'Teacher', UserStatus.INACTIVE),
            (1, 'Librarian', UserStatus.INACTIVE),
        ]):
            staff = Staff(school_id=school_id, login_user=LoginUser(
                first_name='Staff', email=f'deactive{index}@example.com',
                mobile=f'900000000{index}', role=role, status=status))
            self.db.add(staff)
            self.db.flush()
            if school_id == 1 and role != 'Admin' and status == UserStatus.INACTIVE:
                expected.append(staff.id)
        self.db.commit()
        url = '/schools/staff/deactive?school_id=1'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item['id'] for item in response.json()['data']], expected)
        self.assertTrue(all(item['staff_info']['status'] == 'Inactive'
                            for item in response.json()['data']))
        response = self.client.get(url + '&offset=1&limit=1')
        self.assertEqual([item['id'] for item in response.json()['data']], expected[1:])
        self.assertEqual(self.client.get(url + '&offset=2').json()['data'], [])
        self.assertEqual(self.client.get('/schools/staff/deactive?school_id=999').status_code, 404)
        self.assertEqual(self.client.get('/schools/staff/deactive').status_code, 422)
        self.assertEqual(self.client.get('/schools/staff/deactive?school_id=invalid').status_code, 422)
        for params in ({'offset': -1}, {'limit': 0}, {'limit': 201}):
            self.assertEqual(self.client.get('/schools/staff/deactive',
                                             params={'school_id': 1, **params}).status_code, 422)

    def test_deactive_list_tracks_status_changes(self):
        url = '/schools/staff/deactive?school_id=1'
        self.assertEqual(self.client.get(url).json()['data'], [])
        for status, expected in [('Deactive', [self.staff.id]), ('Active', [])]:
            response = self.client.patch(self.url, params=self.params, json={'status': status})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([item['id'] for item in self.client.get(url).json()['data']], expected)

    def test_status_changes_persist_and_control_authentication(self):
        credentials = HTTPAuthorizationCredentials(
            scheme='Bearer', credentials=account_token(self.staff))
        for requested, stored in [('Deactive', UserStatus.INACTIVE),
                                  ('Active', UserStatus.ACTIVE)]:
            response = self.client.patch(self.url, params=self.params,
                                         json={'status': requested})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data'], {
                'user_id': self.staff.login_user_id, 'status': requested})
            self.db.expire_all()
            self.assertEqual(self.staff.status, stored)
            listing = self.client.get('/schools/school/1/staff').json()
            self.assertEqual(listing['data'][0]['staff_info']['status'], stored.value)
            if stored == UserStatus.INACTIVE:
                with self.assertRaises(HTTPException) as error:
                    get_current_user(credentials, self.db)
                self.assertEqual(error.exception.status_code, 403)
            else:
                self.assertEqual(get_current_user(credentials, self.db).id, self.staff.id)

    def test_invalid_status_and_wrong_school_do_not_change_account(self):
        for payload in ({}, {'status': 'Inactive'}, {'status': True},
                        {'status': 'Pending'}, {'status': 'Active', 'role': 'Admin'}):
            response = self.client.patch(self.url, params=self.params, json=payload)
            self.assertEqual(response.status_code, 422, response.text)
        for params in (dict(self.params, school_id=2), dict(self.params, user_id=999)):
            response = self.client.patch(self.url, params=params, json={'status': 'Deactive'})
            self.assertEqual(response.status_code, 404, response.text)
        self.db.expire_all()
        self.assertEqual(self.staff.status, UserStatus.ACTIVE)

    def test_authenticated_roles_can_update_and_no_self_deactivation(self):
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN, 'Teacher'):
            self.user.role = role
            response = self.client.patch(self.url, params=self.params, json={'status': 'Deactive'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.staff.status, UserStatus.INACTIVE)
        response = self.client.patch(self.url, params=self.params, json={'status': 'Active'})
        self.assertEqual(response.status_code, 200, response.text)
        self.user.role = UserRole.SUPER_ADMIN
        self.user.login_user_id = self.staff.login_user_id
        response = self.client.patch(self.url, params=self.params, json={'status': 'Deactive'})
        self.assertEqual(response.status_code, 400, response.text)
        self.db.expire_all()
        self.assertEqual(self.staff.status, UserStatus.ACTIVE)

    def test_status_update_requires_authentication(self):
        del self.client.app.dependency_overrides[get_current_user]
        response = self.client.patch(self.url, params=self.params, json={'status': 'Deactive'})
        self.assertIn(response.status_code, (401, 403))
        self.db.expire_all()
        self.assertEqual(self.staff.status, UserStatus.ACTIVE)

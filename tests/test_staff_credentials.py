import unittest

import test_sessions
from dependencies.auth import account_token
from models.staff import Staff
from models.user import LoginUser, UserRole, UserStatus
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from utils.passwords import hash_password, verify_password


class StaffCredentialTests(unittest.TestCase):
    def setUp(self):
        test_sessions.SessionTests.setUp(self)
        self.staff = Staff(school_id=1, login_user=LoginUser(
            first_name='Staff', email='staff@example.com', mobile='9876543210',
            role='Teacher', password=hash_password('old password')))
        self.db.add(self.staff)
        self.db.commit()
        self.params = {'school_id': 1, 'user_id': self.staff.login_user_id}

    tearDown = test_sessions.SessionTests.tearDown

    def change(self, kind, body, params=None):
        return self.client.patch('/schools/staff/change-' + kind,
                                 params=self.params if params is None else params, json=body)

    def test_password_and_mobile_persist_without_exposing_password(self):
        token = account_token(self.staff)
        response = self.change('password', {'new_password': 'new password'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('password', response.json()['data'])
        self.assertTrue(verify_password('new password', self.staff.password))
        self.assertNotEqual(token, account_token(self.staff))
        password = self.staff.password
        token = account_token(self.staff)
        response = self.change('mobile', {'mobile_number': '9999999999'})
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(self.staff.mobile, '9999999999')
        self.assertEqual(password, self.staff.password)
        self.assertNotEqual(token, account_token(self.staff))

    def test_self_service_requires_current_password(self):
        self.client.app.dependency_overrides[test_sessions.get_current_user] = lambda: self.staff
        for kind, body in [('password', {'new_password': 'new password'}),
                           ('mobile', {'mobile_number': '9999999999'})]:
            self.assertEqual(self.change(kind, body).status_code, 403)
            self.assertEqual(self.change(kind, body | {'current_password': 'wrong'}).status_code, 403)
        self.assertEqual(self.change('mobile', {'mobile_number': '9999999999',
                                              'current_password': 'old password'}).status_code, 200)
        self.assertEqual(self.change('password', {'new_password': 'new password',
                                                'current_password': 'old password'}).status_code, 200)

    def test_duplicate_mobile_is_rejected_across_schools_and_inactive_accounts(self):
        other = Staff(school_id=2, login_user=LoginUser(
            first_name='Other', email='other@example.com', mobile='8888888888',
            role='Teacher', password=hash_password('other password')))
        self.db.add(other)
        self.db.commit()
        original_mobile = self.staff.mobile
        original_password = self.staff.password
        for status in (UserStatus.ACTIVE, UserStatus.INACTIVE):
            other.login_user.status = status
            self.db.commit()
            response = self.change('mobile', {'mobile_number': other.mobile})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail'], 'Mobile already exists')
            self.db.expire_all()
            self.assertEqual(self.staff.mobile, original_mobile)
            self.assertEqual(self.staff.password, original_password)
            self.assertEqual(self.db.query(LoginUser).filter_by(mobile=other.mobile).count(), 1)
        response = self.change('mobile', {'mobile_number': original_mobile})
        self.assertEqual(response.status_code, 200, response.text)

    def test_mapped_login_user_without_staff_profile(self):
        account = self.db.query(test_sessions.User).first().login_user
        self.db.add(SchoolMapping(school_id=1, user_id=account.id,
                                 status=SchoolMappingStatus.ACTIVE))
        self.db.commit()
        params = {'school_id': 1, 'user_id': account.id}
        response = self.change('password', {'new_password': 'updated password'}, params)
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertTrue(verify_password('updated password', account.password))
        response = self.change('mobile', {'mobile_number': '8888888888'}, params)
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(account.mobile, '8888888888')
        self.assertEqual(self.change('password', {'new_password': 'wrong school'},
                                     params | {'school_id': 2}).status_code, 404)
        self.db.query(SchoolMapping).filter_by(user_id=account.id).update(
            {'status': SchoolMappingStatus.DEACTIVE})
        self.db.commit()
        self.assertEqual(self.change('password', {'new_password': 'inactive'}, params).status_code, 404)
        self.assertEqual(self.change('password', {'new_password': 'missing'},
                                     params | {'user_id': 99999}).status_code, 404)

    def test_permissions_duplicates_and_validation(self):
        self.user.role = UserRole.ADMIN
        self.user.login_user_id = self.db.query(test_sessions.User).first().login_user_id
        self.assertEqual(self.change('password', {'new_password': 'new'}).status_code, 403)
        self.db.add(SchoolMapping(school_id=1, user_id=self.user.login_user_id,
                                 status=SchoolMappingStatus.ACTIVE))
        self.db.commit()
        self.assertEqual(self.change('password', {'new_password': 'new'}).status_code, 200)
        other_mobile = self.db.get(LoginUser, self.user.login_user_id).mobile
        self.assertEqual(self.change('mobile', {'mobile_number': other_mobile}).status_code, 409)
        self.assertEqual(self.staff.mobile, '9876543210')
        self.assertEqual(self.change('mobile', {'mobile_number': '123'},
                                     self.params | {'school_id': 2}).status_code, 404)
        for kind, body in [('password', {'new_password': ' '}),
                           ('mobile', {'mobile_number': ' '}),
                           ('mobile', {'mobile_number': '1' * 21}), ('password', {})]:
            self.assertEqual(self.change(kind, body).status_code, 422)
        self.assertEqual(self.change('password', {'new_password': 'new'}, {}).status_code, 422)

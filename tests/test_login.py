import unittest
from datetime import date

import test_users
from models.school import School
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.session import Session as SchoolSession
from models.user import User, UserRole, UserStatus
from routers.auth import router
from schemas.user import UserLoginResponse


class LoginTests(unittest.TestCase):
    def setUp(self):
        test_users.UserDeleteTests.setUp(self)
        self.client.app.include_router(router)
        self.credentials = {"mobile": "2222222222", "password": "secret"}

    tearDown = test_users.UserDeleteTests.tearDown

    def login(self, **changes):
        return self.client.post("/auth/login", json=dict(self.credentials, **changes))

    def assign(self, school_id=1, status=SchoolMappingStatus.ACTIVE):
        self.db.add(SchoolMapping(school_id=school_id, user_id=2, status=status))
        self.db.commit()

    def second_school(self):
        school = self.db.get(School, 1)
        values = {column.name: getattr(school, column.name)
                  for column in School.__table__.columns if column.name != "id"}
        self.db.add(School(id=2, **dict(values, school_name="Second School")))
        self.db.commit()

    def test_invalid_credentials_checked_before_assignments(self):
        for changes in ({"password": "wrong"}, {"mobile": "unknown"}):
            response = self.login(**changes)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("token", response.json())

    def test_missing_assignment_for_admin_and_sub_admin(self):
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.db.get(User, 2).role = role
            self.db.commit()
            response = self.login()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"], "Still no school assigned for this user")

    def test_inactive_and_pending_users(self):
        self.assign()
        for status in (UserStatus.INACTIVE, UserStatus.PENDING):
            self.db.get(User, 2).status = status
            self.db.commit()
            response = self.login()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"],
                             "User not active yet, please contact the administrator")

    def test_deactivated_assignment(self):
        self.assign(status=SchoolMappingStatus.DEACTIVE)
        response = self.login()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"],
                         "User not active yet, please contact the administrator")

    def test_admin_multiple_schools_and_json_serialization(self):
        self.second_school()
        self.assign(2)
        self.assign(1)
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        UserLoginResponse.model_validate(body)
        schools = body["data"]["schools"]
        self.assertEqual([school["id"] for school in schools], [1, 2])
        self.assertEqual(schools[1]["school_name"], "Second School")
        for school in schools:
            for field in ("season", "session_name", "session_start_date", "session_end_date"):
                self.assertNotIn(field, school)
            self.assertEqual(school["sessions"], [])
        self.assertNotIn("merchant_key", schools[0])
        self.assertEqual(response.headers["Authorization"], f'Bearer {body["token"]}')

    def test_only_active_assigned_schools_returned(self):
        self.second_school()
        self.assign(1)
        self.assertEqual(len(self.login().json()["data"]["schools"]), 1)
        self.assign(2, SchoolMappingStatus.DEACTIVE)
        self.assertEqual([school["id"] for school in self.login().json()["data"]["schools"]], [1])

    def test_sessions_come_from_sessions_table_and_match_school(self):
        self.second_school()
        self.assign(1)
        self.db.add_all([
            SchoolSession(id=1, school_id=1, name="2027-28", start_date=date(2027, 4, 1),
                          end_date=date(2028, 3, 31)),
            SchoolSession(id=2, school_id=1, name="2026-27", start_date=date(2026, 4, 1),
                          end_date=date(2027, 3, 31)),
            SchoolSession(id=3, school_id=2, name="Other school", start_date=date(2026, 4, 1),
                          end_date=date(2027, 3, 31)),
        ])
        self.db.commit()
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        UserLoginResponse.model_validate(response.json())
        sessions = response.json()["data"]["schools"][0]["sessions"]
        self.assertEqual([session["id"] for session in sessions], [1, 2])
        self.assertEqual(sessions[0]["name"], "2027-28")
        self.assertEqual(sessions[0]["start_date"], "2027-04-01")
        self.assertEqual(sessions[0]["end_date"], "2028-03-31")
        self.assertTrue(all(session["school_id"] == 1 for session in sessions))
        self.assign(2)
        schools = self.login().json()["data"]["schools"]
        self.assertEqual([session["id"] for session in schools[1]["sessions"]], [3])

    def test_sub_admin_school_is_an_array(self):
        self.db.get(User, 2).role = UserRole.SUB_ADMIN
        self.assign()
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([school["id"] for school in response.json()["data"]["schools"]], [1])

    def test_super_admin_without_assignment(self):
        response = self.login(mobile="1111111111")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("schools", response.json()["data"])
        UserLoginResponse.model_validate(response.json())

    def test_super_admin_with_assignment_omits_schools(self):
        self.db.add(SchoolMapping(school_id=1, user_id=1, status=SchoolMappingStatus.ACTIVE))
        self.db.commit()
        response = self.login(mobile="1111111111")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["id"], 1)
        self.assertNotIn("schools", response.json()["data"])

    def test_argon2_login_and_hash_cannot_be_used_as_password(self):
        from utils.passwords import hash_password
        user = self.db.get(User, 1)
        user.password = hash_password("secret")
        self.db.commit()
        stored = user.password
        self.assertEqual(self.login(mobile=user.mobile).status_code, 200)
        self.assertEqual(user.password, stored)
        self.assertEqual(self.login(mobile=user.mobile, password="wrong").status_code, 401)
        self.assertEqual(self.login(mobile=user.mobile, password=stored).status_code, 401)

    def test_legacy_password_migrates_and_token_still_authenticates(self):
        from fastapi.security import HTTPAuthorizationCredentials
        from dependencies.auth import get_current_user
        from utils.passwords import verify_password
        response = self.login(mobile="1111111111")
        self.assertEqual(response.status_code, 200)
        user = self.db.get(User, 1)
        self.assertTrue(user.password.startswith("$argon2id$"))
        self.assertTrue(verify_password("secret", user.password))
        self.assertNotIn("password", response.json()["data"])
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=response.json()["token"])
        self.assertEqual(get_current_user(credentials, self.db).id, user.id)

    def test_malformed_hash_rejected(self):
        self.db.get(User, 1).password = "$argon2id$invalid"
        self.db.commit()
        self.assertEqual(self.login(mobile="1111111111").status_code, 401)

    def test_register_hashes_explicit_and_default_passwords(self):
        from utils.passwords import verify_password
        for index, extra in enumerate(({}, {"password": None}, {"password": ""}, {"password": "chosen-password"})):
            mobile = f"333333333{index}"
            response = self.client.post("/auth/register", json={
                "first_name": "New", "last_name": "User", "email": f"new{index}@example.com",
                "mobile": mobile, "role": "Super Admin", **extra,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("password", response.json()["data"])
            user = self.db.query(User).filter_by(mobile=mobile).one()
            password = extra.get("password") or mobile
            self.assertTrue(user.password.startswith("$argon2id$"))
            self.assertTrue(verify_password(password, user.password))
            self.assertEqual(self.login(mobile=mobile, password=password).status_code, 200)

    def test_user_password_update_hashes_and_can_log_in(self):
        response = self.client.put("/users/1", json={"password": "updated-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("password", response.json()["data"])
        self.assertTrue(self.db.get(User, 1).password.startswith("$argon2id$"))
        self.assertEqual(self.login(mobile="1111111111", password="updated-password").status_code, 200)
        self.assertEqual(self.login(mobile="1111111111").status_code, 401)

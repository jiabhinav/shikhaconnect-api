import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.database import Base
from database.table_init import register_models
from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School
from models.user import User, UserRole
from routers.users import router


class UserDeleteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        register_models()
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        school_values = {
            "school_name": "Test School",
            "branch_name": "Main",
            "primary_email": "school@example.com",
            "primary_number": "1234567890",
            "start_admission_no": 1,
            "start_employee_no": 1,
            "season": "2025-26",
            "session_name": "2025-26",
            "session_start_date": date(2025, 4, 1),
            "session_end_date": date(2026, 3, 31),
            "principal_name": "Principal",
            "city": "Test City",
            "state": "Test State",
            "country": "Test Country",
        }
        self.db.add(School(id=1, **school_values))
        self.db.add_all(
            [
                User(
                    id=1,
                    first_name="Super",
                    last_name="Admin",
                    email="admin@example.com",
                    mobile="1111111111",
                    password="secret",
                    role=UserRole.SUPER_ADMIN,
                ),
                User(
                    id=2,
                    first_name="Staff",
                    last_name="Member",
                    email="staff@example.com",
                    mobile="2222222222",
                    password="secret",
                    role=UserRole.ADMIN,
                ),
            ]
        )
        self.db.commit()

        self.user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db_session] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_delete_user_without_school_assignments(self):
        self.db.commit()

        response = self.client.delete("/users/2")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.db.query(User).filter_by(id=2).first())

    def test_update_accepts_compact_and_canonical_roles(self):
        for role, expected in (
            ("SubAdmin", UserRole.SUB_ADMIN),
            ("SuperAdmin", UserRole.SUPER_ADMIN),
            ("Sub Admin", UserRole.SUB_ADMIN),
            ("Super Admin", UserRole.SUPER_ADMIN),
            ("Admin", UserRole.ADMIN),
        ):
            with self.subTest(role=role):
                response = self.client.put("/users/2", json={"role": role})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["data"]["role"], expected.value)
                self.db.expire_all()
                self.assertEqual(self.db.get(User, 2).role, expected)

    def test_update_rejects_unknown_role(self):
        response = self.client.put("/users/2", json={"role": "Unknown"})

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.db.get(User, 2).role, UserRole.ADMIN)

    def test_super_admin_route_lists_only_super_admin_users(self):
        self.db.add(
            User(
                id=3,
                first_name="Second",
                last_name="Super",
                email="super2@example.com",
                mobile="3333333333",
                password="secret",
                role=UserRole.SUPER_ADMIN,
            )
        )
        self.db.add(
            User(
                id=4,
                first_name="Normal",
                last_name="User",
                email="user@example.com",
                mobile="4444444444",
                password="secret",
                role=UserRole.ADMIN,
            )
        )
        self.db.commit()

        response = self.client.get("/super-admin/users")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(payload["data"]), 2)
        self.assertEqual({item["id"] for item in payload["data"]}, {1, 3})

    def test_list_super_admins_returns_only_super_admin_users(self):
        self.db.add(
            User(
                id=3,
                first_name="Second",
                last_name="Super",
                email="super2@example.com",
                mobile="3333333333",
                password="secret",
                role=UserRole.SUPER_ADMIN,
            )
        )
        self.db.add(
            User(
                id=4,
                first_name="Normal",
                last_name="User",
                email="user@example.com",
                mobile="4444444444",
                password="secret",
                role=UserRole.ADMIN,
            )
        )
        self.db.commit()

        response = self.client.get("/users/super-admins")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(payload["data"]), 2)
        self.assertEqual({item["id"] for item in payload["data"]}, {1, 3})

    def test_list_super_admins_requires_super_admin_access(self):
        self.user.role = UserRole.ADMIN

        response = self.client.get("/users/super-admins")

        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("Only Super Admin", response.json()["detail"])

    def test_super_admin_cannot_delete_self(self):
        from models.user import LoginUser
        account_id = self.db.get(User, 1).login_user_id
        response = self.client.delete("/users/1")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail'], "Super Admin cannot delete their own account")
        self.assertIsNotNone(self.db.get(User, 1))
        self.assertIsNotNone(self.db.get(LoginUser, account_id))

    def add_related_user_records(self):
        from models.staff import Staff, StaffAddress, StaffPermission
        from models.school_mapping import SchoolMapping, SchoolMappingStatus
        user = self.db.get(User, 2)
        user.address = StaffAddress(city='Delhi')
        user.permissions = [StaffPermission(staff_module_id=10, is_enabled=True)]
        self.db.add(Staff(school_id=1, login_user=user.login_user))
        self.db.add(SchoolMapping(school_id=1, user_id=2, status=SchoolMappingStatus.ACTIVE))
        self.db.commit()
        return user.login_user_id

    def test_delete_removes_account_and_related_records_only(self):
        from models.user import LoginUser
        from models.staff import Staff, StaffAddress, StaffPermission
        from models.school_mapping import SchoolMapping
        account_id = self.add_related_user_records()
        response = self.client.delete('/users/2')
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertIsNone(self.db.get(User, 2))
        self.assertIsNone(self.db.get(LoginUser, account_id))
        for model in (Staff, StaffAddress, StaffPermission):
            self.assertEqual(self.db.query(model).filter_by(login_user_id=account_id).count(), 0)
        self.assertEqual(self.db.query(SchoolMapping).filter_by(user_id=2).count(), 0)
        self.assertIsNotNone(self.db.get(User, 1))
        self.assertIsNotNone(self.db.get(School, 1))

    def test_delete_failure_rolls_back_related_records(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        from models.user import LoginUser
        from models.staff import Staff, StaffAddress, StaffPermission
        from models.school_mapping import SchoolMapping
        account_id = self.add_related_user_records()
        def fail_commit():
            self.db.flush()
            raise IntegrityError('delete', {}, Exception('test failure'))
        with patch.object(self.db, 'commit', side_effect=fail_commit):
            response = self.client.delete('/users/2')
        self.assertEqual(response.status_code, 400)
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(User, 2))
        self.assertIsNotNone(self.db.get(LoginUser, account_id))
        for model in (Staff, StaffAddress, StaffPermission):
            self.assertEqual(self.db.query(model).filter_by(login_user_id=account_id).count(), 1)
        self.assertEqual(self.db.query(SchoolMapping).filter_by(user_id=2).count(), 1)

    def test_get_user_includes_assigned_schools_and_mapping_status(self):
        from models.school_mapping import SchoolMapping, SchoolMappingStatus
        self.assertEqual(self.client.get('/users/2').json()['data']['schools'], [])
        self.db.add(SchoolMapping(user_id=2, school_id=1, status=SchoolMappingStatus.DEACTIVE))
        self.db.commit()
        response = self.client.get('/users/2')
        self.assertEqual(response.status_code, 200, response.text)
        schools = response.json()['data']['schools']
        self.assertEqual(len(schools), 1)
        self.assertEqual(schools[0]['id'], 1)
        self.assertEqual(schools[0]['school_name'], 'Test School')
        self.assertEqual(schools[0]['mapping_status'], 'deactive')
        self.assertNotIn('merchant_key', schools[0])
        self.assertEqual(self.client.get('/users/1').json()['data']['schools'], [])

    def test_list_users_includes_each_users_assigned_schools(self):
        from models.school_mapping import SchoolMapping, SchoolMappingStatus
        self.db.add(SchoolMapping(user_id=2, school_id=1, status=SchoolMappingStatus.ACTIVE))
        self.db.commit()
        response = self.client.get('/users/')
        self.assertEqual(response.status_code, 200, response.text)
        users = {item['id']: item for item in response.json()['data']}
        self.assertEqual(users[1]['schools'], [])
        self.assertEqual([s['id'] for s in users[2]['schools']], [1])
        self.assertEqual(users[2]['schools'][0]['mapping_status'], 'active')
        self.assertEqual(users[2], self.client.get('/users/2').json()['data'])

    def test_status_targets_login_account_including_staff_without_user_profile(self):
        from models.user import LoginUser, UserStatus
        from models.staff import Staff
        self.user = self.db.get(User, 1)
        account = LoginUser(id=50, first_name='Teacher', email='teacher@example.com',
                            mobile='555', password='hash', role='Teacher')
        self.db.add(Staff(school_id=1, login_user=account))
        self.db.commit()
        self.assertIsNone(self.db.get(User, 50))
        for enabled, expected in ((False, UserStatus.INACTIVE), (True, UserStatus.ACTIVE)):
            response = self.client.patch('/users/50/status', json={'status': enabled})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data'], {'id': 50, 'status': expected.value})
            self.db.expire_all()
            self.assertEqual(self.db.get(LoginUser, 50).status, expected)

    def test_status_self_disable_checks_login_id_not_profile_id(self):
        from models.user import LoginUser, UserStatus
        account = LoginUser(id=70, first_name='Other', email='otheradmin@example.com',
                            mobile='777', password='hash', role=UserRole.SUPER_ADMIN)
        actor = User(id=30, login_user=account)
        self.db.add(actor)
        self.db.commit()
        self.user = actor
        response = self.client.patch('/users/70/status', json={'status': False})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()['detail'], 'You cannot disable your own account')
        self.assertEqual(account.status, UserStatus.ACTIVE)
        self.assertEqual(self.client.patch('/users/99999/status', json={'status': False}).status_code, 404)
        self.user = self.db.get(User, 2)
        self.assertEqual(self.client.patch('/users/70/status', json={'status': False}).status_code, 403)

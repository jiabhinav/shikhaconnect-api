import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.database import Base
from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School
from models.user import User, UserRole
from routers.users import router


class UserDeleteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
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

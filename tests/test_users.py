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
from models.school import School, SchoolUserAssignment
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

    def test_delete_user_removes_school_assignments(self):
        self.db.add(SchoolUserAssignment(school_id=1, user_id=2, role=UserRole.ADMIN.value))
        self.db.commit()

        response = self.client.delete("/users/2")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.db.query(SchoolUserAssignment).filter_by(user_id=2).first())
        self.assertIsNone(self.db.query(User).filter_by(id=2).first())

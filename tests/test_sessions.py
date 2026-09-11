import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.database import Base
from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolUserAssignment
from models.session import Session as SchoolSession
from models.user import User, UserRole
from routers.schools import router


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for school_id in (1, 2):
            values = {c.name: (date(2025, 1, 1) if "date" in c.name else 1 if c.name.startswith("start_") else "test")
                      for c in School.__table__.columns if not c.nullable and c.name != "id"}
            self.db.add(School(id=school_id, **values))
        self.db.add(User(id=1, first_name="Test", last_name="Admin", email="test@example.com", mobile="12345"))
        self.db.commit()
        self.user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db_session] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.url = "/schools/school/1/sessions"
        self.payload = {"name": "2025-2026", "start_date": "2025-04-01", "end_date": "2026-03-31"}

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_create_list_update_and_year_duplicates(self):
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        session_id = response.json()["data"]["id"]
        self.assertEqual(response.json()["data"]["school_id"], 1)
        duplicate = dict(self.payload, start_date="2025-06-01", end_date="2026-06-30")
        self.assertEqual(self.client.post(self.url, json=duplicate).status_code, 409)
        self.assertEqual(self.client.post("/schools/school/2/sessions", json=duplicate).status_code, 201)
        self.assertEqual(self.client.put(f"{self.url}/{session_id}", json=dict(self.payload, name="Updated")).status_code, 200)
        self.assertEqual(self.client.get(self.url).json()["data"][0]["name"], "Updated")
        other = self.client.post(self.url, json=dict(self.payload, start_date="2027-01-01", end_date="2028-01-01")).json()["data"]["id"]
        self.assertEqual(self.client.put(f"{self.url}/{other}", json=duplicate).status_code, 409)

    def test_invalid_input_and_missing_resources(self):
        for changes in ({"name": "  "}, {"end_date": "2024-01-01"}, {"start_date": "bad"}, {"name": None}):
            self.assertEqual(self.client.post(self.url, json=dict(self.payload, **changes)).status_code, 422)
        self.assertEqual(self.client.post("/schools/school/999/sessions", json=self.payload).status_code, 404)
        self.assertEqual(self.client.put(f"{self.url}/999", json=self.payload).status_code, 404)

    def test_school_access_and_session_scope(self):
        session_id = self.client.post("/schools/school/2/sessions", json=self.payload).json()["data"]["id"]
        self.assertEqual(self.client.put(f"{self.url}/{session_id}", json=self.payload).status_code, 404)
        self.user.role = UserRole.ADMIN
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, json=self.payload).status_code, 404)
        self.db.add(SchoolUserAssignment(school_id=1, user_id=1, role=UserRole.ADMIN.value))
        self.db.commit()
        self.assertEqual(self.client.post(self.url, json=self.payload).status_code, 201)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get("/schools/school/2/sessions").status_code, 404)

    def test_database_constraints(self):
        self.client.post(self.url, json=self.payload)
        for values in (
            dict(school_id=1, start_date=date(2025, 6, 1), end_date=date(2026, 6, 1)),
            dict(school_id=999, start_date=date(2030, 1, 1), end_date=date(2031, 1, 1)),
            dict(school_id=1, start_date=date(2030, 1, 1), end_date=date(2029, 1, 1)),
        ):
            self.db.add(SchoolSession(name="Test", **values))
            with self.assertRaises(IntegrityError):
                self.db.commit()
            self.db.rollback()

    def test_status(self):
        today = date.today()
        self.assertEqual(SchoolSession(start_date=today, end_date=today).status, "Current")
        self.assertEqual(SchoolSession(start_date=date(2000, 1, 1), end_date=date(2001, 1, 1)).status, "Past")
        self.assertEqual(SchoolSession(start_date=date(9998, 1, 1), end_date=date(9999, 1, 1)).status, "Upcoming")

    def test_missing_table_is_created_for_same_day_payload(self):
        SchoolSession.__table__.drop(self.engine)
        payload = dict(self.payload, start_date="2026-09-11", end_date="2026-09-11")
        response = self.client.post(self.url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.get(self.url).json()["data"][0]["start_date"], "2026-09-11")
        self.assertEqual(self.client.post(self.url, json=payload).status_code, 409)

    def test_table_initialization_failure_is_service_unavailable(self):
        with patch("routers.schools.ensure_session_table", side_effect=OperationalError("create", {}, Exception("denied"))):
            with self.assertLogs("routers.schools", level="ERROR"):
                response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 503)

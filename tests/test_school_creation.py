import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.database import Base
from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolPermission
from models.session import Session as SchoolSession
from models.user import UserRole
from routers.schools import router as school_router
from routers.super_admin import router as super_admin_router


class SchoolCreationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        app = FastAPI()
        app.include_router(school_router)
        app.include_router(super_admin_router)
        app.dependency_overrides[get_db_session] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.payload = {
            "school_info": {
                "school_name": "Test School", "branch_name": "Main",
                "primary_email": "school@example.com", "primary_number": "1234567890",
                "start_admission_no": 1, "start_employee_no": 1,
                "season": "2026-27", "session_name": "2026-27",
                "session_start_date": "2026-04-01", "session_end_date": "2027-03-31",
                "principal_name": "Principal",
            },
            "address": {"city": "Delhi", "state": "Delhi", "country": "India"},
            "services": [1],
        }

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_creation_inserts_session_for_each_school(self):
        for index, prefix in enumerate(("/schools", "/super-admin")):
            with self.subTest(prefix=prefix):
                self.payload["school_info"]["primary_email"] = f"school{index}@example.com"
                response = self.client.post(f"{prefix}/create_school", json=self.payload)
                self.assertEqual(response.status_code, 200, response.text)
                school_id = response.json()["data"]["id"]
                session = self.db.query(SchoolSession).filter_by(school_id=school_id).one()
                self.assertEqual(session.name, "2026-27")
                self.assertEqual(session.start_date, date(2026, 4, 1))
                self.assertEqual(session.end_date, date(2027, 3, 31))

    def test_get_schools_includes_only_current_year_sessions(self):
        year = 2026
        for module in ("routers.schools", "routers.super_admin"):
            clock = patch(f"{module}.date", wraps=date)
            mocked_date = clock.start()
            mocked_date.today.return_value = date(year, 9, 14)
            self.addCleanup(clock.stop)
        school_ids = []
        for index in range(2):
            payload = self.payload | {"school_info": self.payload["school_info"] | {
                "primary_email": f"sessions{index}@example.com",
                "session_start_date": f"{year}-04-01",
                "session_end_date": f"{year + 1}-03-31"}}
            response = self.client.post("/super-admin/create_school", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            school_ids.append(response.json()["data"]["id"])
        self.db.add_all([
            SchoolSession(school_id=school_ids[0], name="History",
                start_date=date(year - 2, 4, 1), end_date=date(year - 1, 12, 31)),
            SchoolSession(school_id=school_ids[0], name="Future",
                start_date=date(year + 1, 1, 1), end_date=date(year + 2, 3, 31)),
            SchoolSession(school_id=school_ids[0], name="Ends this year",
                start_date=date(year - 1, 4, 1), end_date=date(year, 1, 1)),
            SchoolSession(school_id=school_ids[0], name="Starts this year",
                start_date=date(year, 12, 31), end_date=date(year + 1, 3, 31)),
        ])
        self.db.query(SchoolSession).filter_by(school_id=school_ids[1]).delete()
        self.db.commit()
        for prefix in ("/schools", "/super-admin"):
            with self.subTest(prefix=prefix):
                response = self.client.get(f"{prefix}/school")
                self.assertEqual(response.status_code, 200, response.text)
                schools = {school["id"]: school for school in response.json()["data"]}
                session = schools[school_ids[0]]["sessions"]
                self.assertIsInstance(session, dict)
                self.assertEqual(session["name"], "2026-27")
                self.assertEqual(session["school_id"], school_ids[0])
                self.assertEqual(session["start_date"], f"{year}-04-01")
                self.assertEqual(session["end_date"], f"{year + 1}-03-31")
                self.assertIn("id", session)
                self.assertIn("status", session)
                self.assertIsNone(schools[school_ids[1]]["sessions"])
                for school_id in school_ids:
                    response = self.client.get(f"{prefix}/school/{school_id}")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["data"]["sessions"], schools[school_id]["sessions"])

    def test_session_failure_rolls_back_school_and_permissions(self):
        def fail_insert(mapper, connection, target):
            raise RuntimeError("Session insert failed")

        event.listen(SchoolSession, "before_insert", fail_insert)
        try:
            for prefix in ("/schools", "/super-admin"):
                with self.subTest(prefix=prefix):
                    response = self.client.post(f"{prefix}/create_school", json=self.payload)
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(self.db.query(School).count(), 0)
                    self.assertEqual(self.db.query(SchoolPermission).count(), 0)
                    self.assertEqual(self.db.query(SchoolSession).count(), 0)
        finally:
            event.remove(SchoolSession, "before_insert", fail_insert)

    def test_school_list_includes_enabled_and_disabled_permissions(self):
        self.db.execute(text("CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT)"))
        self.db.execute(text("INSERT INTO modules VALUES (1, 'Students'), (2, 'Fees')"))
        self.db.commit()
        response = self.client.post('/super-admin/create_school', json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        school_id = response.json()['data']['id']
        self.db.add(SchoolPermission(school_id=school_id, module_id=2, is_enabled=False))
        self.db.commit()
        response = self.client.get('/super-admin/school')
        self.assertEqual(response.status_code, 200, response.text)
        school = response.json()['data'][0]
        self.assertTrue(set(School.__table__.columns.keys()).issubset(school))
        self.assertEqual(school['principal_name'], 'Principal')
        self.assertEqual([p['name'] for p in school['permissions']], ['Students', 'Fees'])
        self.assertEqual([p['module_id'] for p in school['permissions']], [1, 2])
        self.assertEqual([p['is_enabled'] for p in school['permissions']], [True, False])
        self.assertTrue(all(p['school_id'] == school_id for p in school['permissions']))
        self.client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=school_id, role=UserRole.ADMIN)
        response = self.client.get('/super-admin/school')
        self.assertEqual(response.status_code, 403)

    def test_update_and_delete_without_assignments(self):
        for prefix in ('/schools', '/super-admin'):
            with self.subTest(prefix=prefix):
                response = self.client.post(f'{prefix}/create_school', json=self.payload)
                self.assertEqual(response.status_code, 200, response.text)
                school_id = response.json()['data']['id']
                self.assertNotIn('admins', response.json()['data'])
                updated = dict(self.payload, services=[2, 3])
                response = self.client.put(f'{prefix}/update_school/{school_id}', json=updated)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(sorted(p['module_id'] for p in response.json()['data']['permissions']), [2, 3])
                self.assertTrue(all(p['name'] is None for p in response.json()['data']['permissions']))
                response = self.client.delete(f'{prefix}/delete_school/{school_id}')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.db.query(SchoolPermission).count(), 0)
                self.assertEqual(self.db.query(SchoolSession).count(), 0)

    def test_update_synchronizes_session_and_preserves_other_sessions(self):
        for index, prefix in enumerate(("/schools", "/super-admin")):
            with self.subTest(prefix=prefix):
                self.payload["school_info"]["primary_email"] = f"update{index}@example.com"
                response = self.client.post(f"{prefix}/create_school", json=self.payload)
                self.assertEqual(response.status_code, 200, response.text)
                school_id = response.json()["data"]["id"]
                original = self.db.query(SchoolSession).filter_by(school_id=school_id).one()
                original_id = original.id
                historical = SchoolSession(school_id=school_id, name="History",
                    start_date=date(2024, 4, 1), end_date=date(2025, 3, 31))
                self.db.add(historical)
                self.db.commit()
                updated = self.payload | {"school_info": self.payload["school_info"] | {
                    "session_name": "Updated", "session_start_date": "2027-04-01",
                    "session_end_date": "2028-03-31"}, "services": [1, 2]}
                response = self.client.put(f"{prefix}/update_school/{school_id}", json=updated)
                self.assertEqual(response.status_code, 200, response.text)
                self.db.refresh(original)
                self.db.refresh(historical)
                self.assertEqual(original.id, original_id)
                self.assertEqual(original.name, "Updated")
                self.assertEqual(original.start_date, date(2027, 4, 1))
                self.assertEqual(original.end_date, date(2028, 3, 31))
                self.assertEqual(historical.name, "History")
                self.assertEqual(self.db.query(SchoolSession).filter_by(school_id=school_id).count(), 2)
                # The same year pair must be rejected even when month/day differ.
                updated["school_info"].update(session_start_date="2024-05-01",
                    session_end_date="2025-02-28", school_name="Rejected")
                updated["services"] = [3]
                response = self.client.put(f"{prefix}/update_school/{school_id}", json=updated)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["start_year"], 2024)
                self.db.expire_all()
                school = self.db.get(School, school_id)
                self.assertEqual(school.school_name, "Test School")
                self.assertEqual(original.start_date, date(2027, 4, 1))
                self.assertEqual(historical.name, "History")
                self.assertEqual(sorted(p.module_id for p in school.permissions), [1, 2])

    def test_update_removes_legacy_year_index(self):
        for index, prefix in enumerate(("/schools", "/super-admin")):
            with self.subTest(prefix=prefix):
                self.payload["school_info"]["primary_email"] = f"legacy{index}@example.com"
                response = self.client.post(f"{prefix}/create_school", json=self.payload)
                self.assertEqual(response.status_code, 200, response.text)
                school_id = response.json()["data"]["id"]
                self.db.add(SchoolSession(school_id=school_id, name="Other",
                    start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)))
                self.db.execute(text("CREATE UNIQUE INDEX uq_session_school_years ON sessions "
                    "(school_id, strftime('%Y', start_date), strftime('%Y', end_date))"))
                self.db.commit()
                updated = self.payload | {"school_info": self.payload["school_info"] | {
                    "session_start_date": "2028-01-01", "session_end_date": "2028-12-31"}}
                response = self.client.put(f"{prefix}/update_school/{school_id}", json=updated)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.db.query(SchoolSession).filter_by(school_id=school_id).count(), 2)
                self.assertIsNone(self.db.execute(text("SELECT name FROM sqlite_master "
                    "WHERE name = 'uq_session_school_years'")).scalar())
                self.db.query(SchoolSession).filter_by(school_id=school_id).delete()
                self.db.commit()

    def test_update_creates_missing_session(self):
        response = self.client.post("/super-admin/create_school", json=self.payload)
        school_id = response.json()["data"]["id"]
        self.db.query(SchoolSession).filter_by(school_id=school_id).delete()
        self.db.commit()
        response = self.client.put(f"/super-admin/update_school/{school_id}", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        session = self.db.query(SchoolSession).filter_by(school_id=school_id).one()
        self.assertEqual(session.name, "2026-27")

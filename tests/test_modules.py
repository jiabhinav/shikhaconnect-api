import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from routers.super_admin import router


class ModulesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.db = Session(self.engine)
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db_session] = lambda: self.db
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_list_existing_table_columns_and_empty_result(self):
        self.db.execute(text("CREATE TABLE modules (id INTEGER PRIMARY KEY, module_name TEXT, is_active BOOLEAN)"))
        self.db.commit()
        self.assertEqual(self.client.get("/super-admin/modules").json()["data"], [])
        self.db.execute(text("INSERT INTO modules VALUES (2, 'Fees', 1), (1, 'Students', 1)"))
        self.db.commit()
        response = self.client.get("/super-admin/modules")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [
            {"id": 1, "module_name": "Students", "is_active": True},
            {"id": 2, "module_name": "Fees", "is_active": True},
        ])

    def test_missing_table(self):
        response = self.client.get("/super-admin/modules")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Modules table is unavailable")

    def test_authentication_required(self):
        del self.app.dependency_overrides[get_current_user]
        self.assertIn(self.client.get("/super-admin/modules").status_code, (401, 403))

import unittest
from unittest.mock import patch

import test_sessions
from database import database
from dependencies.db import get_db_session
from models.house import House
from models.school import SchoolUserAssignment
from models.user import UserRole


class HouseTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def test_crud_and_duplicates(self):
        url = "/schools/school/1/houses"
        response = self.client.post(url, json={"name": " SAPPHIRE "})
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["data"]
        self.assertEqual(item["name"], "SAPPHIRE")
        self.assertEqual(item["school_id"], 1)
        self.assertEqual(self.client.post(url, json={"name": "sapphire"}).status_code, 409)
        other = self.client.post(url, json={"name": "EMERALD"}).json()["data"]["id"]
        self.assertEqual(self.client.put(f"{url}/{other}", json={"name": "Sapphire"}).status_code, 409)
        item_url = f"{url}/{item['id']}"
        self.assertEqual(self.client.get(item_url).json()["data"], item)
        self.assertEqual(self.client.get(f"/schools/school/2/houses/{item['id']}").status_code, 404)
        self.assertEqual(self.client.put(item_url, json={"name": "GARNET"}).status_code, 200)
        self.assertEqual(self.client.get(url).json()["data"][0]["name"], "GARNET")
        self.assertEqual(self.client.delete(item_url).status_code, 200)
        self.assertEqual(self.client.delete(item_url).status_code, 404)
        self.assertEqual(self.client.get(item_url).status_code, 404)
        self.assertEqual(self.client.put(item_url, json={"name": "X"}).status_code, 404)

    def test_admin_sub_admin_and_school_scope(self):
        url = "/schools/school/1/houses"
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.post(url, json={"name": "EMERALD"}).status_code, 404)
            assignment = SchoolUserAssignment(school_id=1, user_id=1, role=role.value)
            self.db.add(assignment)
            self.db.commit()
            response = self.client.post(url, json={"name": "EMERALD"})
            self.assertEqual(response.status_code, 201, response.text)
            item_id = response.json()["data"]["id"]
            self.assertEqual(self.client.get(url).status_code, 200)
            other = f"/schools/school/2/houses/{item_id}"
            self.assertEqual(self.client.put(other, json={"name": "X"}).status_code, 404)
            self.assertEqual(self.client.delete(other).status_code, 404)
            self.assertEqual(self.client.put(f"{url}/{item_id}", json={"name": "GARNET"}).status_code, 200)
            self.assertEqual(self.client.delete(f"{url}/{item_id}").status_code, 200)
            self.db.delete(assignment)
            self.db.commit()

    def test_auto_creation_through_database_dependency(self):
        House.__table__.drop(self.engine)
        # Exercise the real dependency that creates tables before route queries.
        del self.client.app.dependency_overrides[get_db_session]
        with patch.object(database, "engine", self.engine), patch.object(database, "SessionLocal", lambda: database.sessionmaker(bind=self.engine)()):
            response = self.client.post("/schools/school/1/houses", json={"name": "EMERALD"})
        self.assertEqual(response.status_code, 201, response.text)

    def test_validation_and_same_name_other_school(self):
        url = "/schools/school/1/houses"
        for name in ("", "  ", "x" * 101, None):
            self.assertEqual(self.client.post(url, json={"name": name}).status_code, 422)
        for school_id in (1, 2):
            self.assertEqual(self.client.post(f"/schools/school/{school_id}/houses", json={"name": "EMERALD"}).status_code, 201)

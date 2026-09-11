import unittest

import test_sessions
from models.class_section import SchoolClass, Section
from models.user import UserRole


class ClassSectionTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def test_crud_and_duplicates(self):
        for resource in ("classes", "sections"):
            url = f"/schools/school/1/{resource}"
            created = self.client.post(url, json={"name": " A "})
            self.assertEqual(created.status_code, 201, created.text)
            item_id = created.json()["data"]["id"]
            self.assertEqual(created.json()["data"]["school_id"], 1)
            self.assertEqual(self.client.post(url, json={"name": "a"}).status_code, 409)
            self.assertEqual(self.client.post(f"/schools/school/2/{resource}", json={"name": "A"}).status_code, 201)
            self.assertEqual(self.client.put(f"{url}/{item_id}", json={"name": "B"}).status_code, 200)
            self.assertEqual(self.client.get(url).json()["data"][0]["name"], "B")
            other = self.client.post(url, json={"name": "C"}).json()["data"]["id"]
            self.assertEqual(self.client.put(f"{url}/{other}", json={"name": "b"}).status_code, 409)
            self.assertEqual(self.client.delete(f"{url}/{item_id}").status_code, 200)
            self.assertEqual(self.client.delete(f"{url}/{item_id}").status_code, 404)

    def test_order(self):
        url = "/schools/school/1/classes"
        first = self.client.post(url, json={"name": "PLAY"}).json()["data"]
        second = self.client.post(url, json={"name": "NURSERY"}).json()["data"]
        self.assertEqual([first["class_order"], second["class_order"]], [1, 2])
        response = self.client.put(f"{url}/{first['id']}", json={"name": "PLAY", "class_order": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(url).json()["data"][0]["id"], second["id"])
        self.assertEqual(self.client.put(f"{url}/{first['id']}", json={"name": "Play"}).json()["data"]["class_order"], 3)
        self.assertEqual(self.client.post(url, json={"name": "bad", "class_order": 0}).status_code, 422)

    def test_auto_creation_validation_and_access(self):
        for model in (SchoolClass, Section):
            model.__table__.drop(self.engine)
        for resource in ("classes", "sections"):
            url = f"/schools/school/1/{resource}"
            self.assertEqual(self.client.get(url).json()["data"], [])
            self.assertEqual(self.client.post(url, json={"name": "  "}).status_code, 422)
            response = self.client.post(url, json={"name": "A"})
            self.assertEqual(response.status_code, 201, response.text)
            item_id = response.json()["data"]["id"]
            other = f"/schools/school/2/{resource}/{item_id}"
            self.assertEqual(self.client.put(other, json={"name": "B"}).status_code, 404)
            self.assertEqual(self.client.delete(other).status_code, 404)
        self.user.role = UserRole.ADMIN
        for resource in ("classes", "sections"):
            url = f"/schools/school/1/{resource}"
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, json={"name": "B"}).status_code, 404)
            self.assertEqual(self.client.put(f"{url}/1", json={"name": "B"}).status_code, 404)
            self.assertEqual(self.client.delete(f"{url}/1").status_code, 404)

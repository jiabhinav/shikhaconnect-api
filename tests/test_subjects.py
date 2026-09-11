import unittest
import test_sessions
from models.subject import Subject
from models.school import SchoolUserAssignment
from models.user import UserRole


class SubjectTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def test_admin_and_sub_admin_crud_status(self):
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            assignment = SchoolUserAssignment(school_id=1, user_id=1, role=role.value)
            self.db.add(assignment)
            self.db.commit()
            url = "/schools/school/1/subjects"
            response = self.client.post(url, json={"name": "English", "code": "ENG"})
            self.assertEqual(response.status_code, 201, response.text)
            data = response.json()["data"]
            self.assertEqual(data["status"], "Active")
            item_url = f"{url}/{data['id']}"
            self.assertEqual(self.client.patch(item_url + "/status", json={"status": "Inactive"}).status_code, 200)
            self.assertEqual(self.client.get(url + "?status=Active").json()["data"], [])
            self.assertEqual(len(self.client.get(url + "?status=Inactive").json()["data"]), 1)
            edited = self.client.put(item_url, json={"name": "English language"})
            self.assertEqual(edited.status_code, 200)
            self.assertEqual(edited.json()["data"]["status"], "Inactive")
            self.assertEqual(self.client.patch(item_url + "/status", json={"status": "Active"}).status_code, 200)
            self.assertEqual(self.client.delete(item_url).status_code, 200)
            self.assertEqual(self.client.delete(item_url).status_code, 404)
            self.db.delete(assignment)
            self.db.commit()

    def test_auto_creation_duplicates_and_validation(self):
        Subject.__table__.drop(self.engine)
        url = "/schools/school/1/subjects"
        self.assertEqual(self.client.get(url).json()["data"], [])
        response = self.client.post(url, json={"name": " English ", "code": " ENG "})
        self.assertEqual(response.status_code, 201)
        for payload in ({"name": "english"}, {"name": "Other", "code": "eng"}):
            self.assertEqual(self.client.post(url, json=payload).status_code, 409)
        other = self.client.post(url, json={"name": "Math", "code": " "}).json()["data"]
        self.assertIsNone(other["code"])
        self.assertEqual(self.client.post(url, json={"name": "Science"}).status_code, 201)
        self.assertEqual(self.client.put(f"{url}/{other['id']}", json={"name": "English"}).status_code, 409)
        for payload in ({"name": " "}, {"name": "Math", "status": "bad"}):
            self.assertEqual(self.client.post(url, json=payload).status_code, 422)
        self.assertEqual(self.client.get(url + "?status=bad").status_code, 422)
        self.assertEqual(self.client.post("/schools/school/2/subjects", json={"name": "English", "code": "ENG"}).status_code, 201)

    def test_school_isolation(self):
        url = "/schools/school/1/subjects"
        item_id = self.client.post(url, json={"name": "English"}).json()["data"]["id"]
        other = f"/schools/school/2/subjects/{item_id}"
        self.assertEqual(self.client.put(other, json={"name": "X"}).status_code, 404)
        self.assertEqual(self.client.patch(other + "/status", json={"status": "Inactive"}).status_code, 404)
        self.assertEqual(self.client.delete(other).status_code, 404)
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, json={"name": "X"}).status_code, 404)
            self.assertEqual(self.client.put(f"{url}/{item_id}", json={"name": "X"}).status_code, 404)
            self.assertEqual(self.client.patch(f"{url}/{item_id}/status", json={"status": "Inactive"}).status_code, 404)
            self.assertEqual(self.client.delete(f"{url}/{item_id}").status_code, 404)

import unittest
from unittest.mock import patch

import test_sessions
import test_students
from models.session import Session as SchoolSession
from models.student import Student
from sqlalchemy.exc import IntegrityError


class SessionDeleteTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def create_session(self):
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    def test_delete_unused_session_and_repeat(self):
        session_id = self.create_session()
        response = self.client.delete(f"{self.url}/{session_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], {"id": session_id})
        self.assertIsNone(self.db.get(SchoolSession, session_id))
        self.assertEqual(self.client.delete(f"{self.url}/{session_id}").status_code, 404)

    def test_assigned_session_cannot_be_deleted(self):
        payload = test_students.StudentTests.student_payload(self)
        response = self.client.post('/schools/school/1/students', json=test_students.nest(payload))
        self.assertEqual(response.status_code, 201, response.text)
        session_id = payload['session_id']
        response = self.client.delete(f"{self.url}/{session_id}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"],
                         "Session already assigned to a student. You cannot delete this session.")
        self.assertIsNotNone(self.db.get(SchoolSession, session_id))
        self.assertEqual(self.db.query(Student).count(), 1)
        # A student in another session must not prevent deletion of an unused session.
        self.payload = dict(self.payload, start_date="2030-04-01", end_date="2031-03-31")
        unused_id = self.create_session()
        self.assertEqual(self.client.delete(f"{self.url}/{unused_id}").status_code, 200)

    def test_school_scope_and_missing_resources(self):
        session_id = self.create_session()
        for url in (f'/schools/school/2/sessions/{session_id}',
                    f'/schools/school/999/sessions/{session_id}', f'{self.url}/999'):
            self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertIsNotNone(self.db.get(SchoolSession, session_id))

    def test_requires_authentication(self):
        from dependencies.auth import get_current_user
        session_id = self.create_session()
        del self.client.app.dependency_overrides[get_current_user]
        self.assertIn(self.client.delete(f"{self.url}/{session_id}").status_code, (401, 403))
        self.assertIsNotNone(self.db.get(SchoolSession, session_id))

    def test_database_constraint_conflict_rolls_back(self):
        session_id = self.create_session()
        with patch.object(self.db, 'commit', side_effect=IntegrityError('delete', {}, Exception('restricted'))):
            response = self.client.delete(f"{self.url}/{session_id}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIsNotNone(self.db.get(SchoolSession, session_id))

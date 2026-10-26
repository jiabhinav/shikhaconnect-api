import unittest
from unittest.mock import patch

import catalog_setup
import test_sessions
from database import database
from dependencies.db import get_db_session
from models.fee_head import FeeHead


class FeeHeadTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown
    fee_url = '/schools/fee_heads?school_id=1&session_id=1'
    fee_payload = {'fee_head_name': ' Tuition ', 'student_type': ' New '}

    def test_crud_duplicates_and_scope(self):
        response = self.client.post(self.fee_url, json=self.fee_payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual(item['fee_head_name'], 'Tuition')
        self.assertEqual(item['student_type'], 'New')
        self.assertEqual(self.client.post(self.fee_url, json=dict(self.fee_payload, fee_head_name='tuition', student_type='new')).status_code, 409)
        for scope in ('school_id=1&session_id=3', 'school_id=2&session_id=2'):
            self.assertEqual(self.client.post('/schools/fee_heads?' + scope, json=self.fee_payload).status_code, 201)
        self.assertEqual(self.client.post(self.fee_url, json=dict(self.fee_payload, student_type='Old')).status_code, 201)
        for scope in ('school_id=1&session_id=3', 'school_id=2&session_id=2'):
            url = f"/schools/fee_heads/{item['id']}?{scope}"
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.put(url, json=self.fee_payload).status_code, 404)
            self.assertEqual(self.client.delete(url).status_code, 404)
        url = f"/schools/fee_heads/{item['id']}?school_id=1&session_id=1"
        self.assertEqual(self.client.get(url).json()['data'], item)
        self.assertEqual(self.client.put(url, json=dict(self.fee_payload, student_type='Old')).status_code, 409)
        response = self.client.put(url, json={'fee_head_name': 'Transport', 'student_type': 'All'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['student_type'], 'All')
        self.assertEqual(len(self.client.get(self.fee_url).json()['data']), 2)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 404)

    def test_validation_and_missing_scope(self):
        for field in self.fee_payload:
            for value in ('', '  ', None, 'x' * 101):
                self.assertEqual(self.client.post(self.fee_url, json=dict(self.fee_payload, **{field: value})).status_code, 422)
            self.assertEqual(self.client.post(self.fee_url, json={k: v for k, v in self.fee_payload.items() if k != field}).status_code, 422)
        for scope in ('school_id=999&session_id=1', 'school_id=1&session_id=2', 'school_id=1&session_id=999'):
            self.assertEqual(self.client.post('/schools/fee_heads?' + scope, json=self.fee_payload).status_code, 404)
        self.assertEqual(self.client.get('/schools/fee_heads').status_code, 422)
        self.assertEqual(self.client.get('/schools/school/1/sessions/1/fee_heads').status_code, 200)

    def test_auto_creation_through_database_dependency(self):
        FeeHead.__table__.drop(self.engine)
        del self.client.app.dependency_overrides[get_db_session]
        with patch.object(database, 'engine', self.engine), patch.object(database, 'SessionLocal', lambda: database.sessionmaker(bind=self.engine)()):
            response = self.client.post(self.fee_url, json=self.fee_payload)
        self.assertEqual(response.status_code, 201, response.text)

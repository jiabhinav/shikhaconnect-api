import unittest
from unittest.mock import patch

import catalog_setup
import test_sessions
from database import database
from dependencies.db import get_db_session
from models.fee_structure import FeeStructure


class FeeStructureTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown
    structure_url = '/schools/fee_structures?school_id=1&session_id=1'
    structure_payload = {'fee_structure_name': ' Annual Fees '}

    def test_crud_duplicates_and_scope(self):
        response = self.client.post(self.structure_url, json=self.structure_payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual(item['fee_structure_name'], 'Annual Fees')
        self.assertEqual((item['school_id'], item['session_id']), (1, 1))
        self.assertEqual(self.client.post(self.structure_url, json={'fee_structure_name': 'annual fees'}).status_code, 409)
        for scope in ('school_id=1&session_id=3', 'school_id=2&session_id=2'):
            self.assertEqual(self.client.post('/schools/fee_structures?' + scope, json=self.structure_payload).status_code, 201)
            url = f"/schools/fee_structures/{item['id']}?{scope}"
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.put(url, json=self.structure_payload).status_code, 404)
            self.assertEqual(self.client.delete(url).status_code, 404)
        other = self.client.post(self.structure_url, json={'fee_structure_name': 'Monthly Fees'}).json()['data']['id']
        self.assertEqual(self.client.put(f'/schools/fee_structures/{other}?school_id=1&session_id=1', json=self.structure_payload).status_code, 409)
        url = f"/schools/fee_structures/{item['id']}?school_id=1&session_id=1"
        self.assertEqual(self.client.get(url).json()['data'], item)
        response = self.client.put(url, json={'fee_structure_name': ' Updated Fees '})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['fee_structure_name'], 'Updated Fees')
        self.assertEqual(len(self.client.get(self.structure_url).json()['data']), 2)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_validation_and_missing_scope(self):
        for body in ({}, {'fee_structure_name': ''}, {'fee_structure_name': '  '}, {'fee_structure_name': None}, {'fee_structure_name': 'x' * 101}, dict(self.structure_payload, unknown='x')):
            self.assertEqual(self.client.post(self.structure_url, json=body).status_code, 422)
        for scope in ('school_id=999&session_id=1', 'school_id=1&session_id=2', 'school_id=1&session_id=999'):
            self.assertEqual(self.client.post('/schools/fee_structures?' + scope, json=self.structure_payload).status_code, 404)
        self.assertEqual(self.client.get('/schools/fee_structures').status_code, 422)
        self.assertEqual(self.client.get('/schools/school/1/sessions/1/fee_structures').status_code, 200)

    def test_auto_creation_through_database_dependency(self):
        FeeStructure.__table__.drop(self.engine)
        del self.client.app.dependency_overrides[get_db_session]
        with patch.object(database, 'engine', self.engine), patch.object(database, 'SessionLocal', lambda: database.sessionmaker(bind=self.engine)()):
            response = self.client.post(self.structure_url, json=self.structure_payload)
        self.assertEqual(response.status_code, 201, response.text)

    def test_carry_forward_is_removed_from_api_and_existing_table(self):
        from sqlalchemy import inspect, text
        from database.table_init import ensure_all_tables

        response = self.client.post(self.structure_url, json=self.structure_payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertNotIn('carry_forward', item)
        self.assertEqual(self.client.post(self.structure_url, json=dict(self.structure_payload, carry_forward=True)).status_code, 422)
        self.db.close()
        with self.engine.begin() as connection:
            connection.execute(text('ALTER TABLE fee_structures ADD COLUMN carry_forward BOOLEAN NOT NULL DEFAULT true'))
            ensure_all_tables(connection)
            ensure_all_tables(connection)
            self.assertNotIn('carry_forward', {column['name'] for column in inspect(connection).get_columns('fee_structures')})
        self.db.expire_all()
        row = self.db.query(FeeStructure).filter_by(id=item['id']).one()
        self.assertEqual(row.fee_structure_name, 'Annual Fees')
        url = f"/schools/fee_structures/{item['id']}?school_id=1&session_id=1"
        self.assertNotIn('carry_forward', self.client.get(url).json()['data'])


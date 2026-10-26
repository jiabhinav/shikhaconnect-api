import unittest
from unittest.mock import patch

import catalog_setup
import test_sessions
from database import database
from dependencies.db import get_db_session
from models.class_section import SchoolClass
from models.fee_assignment import FeeAssignment
from models.fee_structure import FeeStructure


class FeeAssignmentTests(unittest.TestCase):
    tearDown = test_sessions.SessionTests.tearDown

    def setUp(self):
        catalog_setup.setUp(self)
        self.db.add_all([
            SchoolClass(id=1, school_id=1, session_id=1, name='Class I', class_order=1),
            SchoolClass(id=2, school_id=2, session_id=2, name='Other school', class_order=1),
            SchoolClass(id=3, school_id=1, session_id=3, name='Other session', class_order=1),
            FeeStructure(id=1, school_id=1, session_id=1, fee_structure_name='Annual'),
            FeeStructure(id=2, school_id=2, session_id=2, fee_structure_name='Other'),
            FeeStructure(id=3, school_id=1, session_id=1, fee_structure_name='Monthly'),
        ])
        self.db.commit()
        self.assignment_url = '/schools/fee_assignments?school_id=1&session_id=1'
        self.assignment_body = {'class_id': 1, 'fee_structure_id': 1}

    def test_crud_filters_and_conflicts(self):
        response = self.client.post(self.assignment_url, json=self.assignment_body)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual(item['class_name'], 'Class I')
        self.assertEqual(item['fee_structure_name'], 'Annual')
        self.assertNotIn('admission_amount', item)
        url = self.assignment_url + f"&assignment_id={item['id']}"
        self.assertEqual(self.client.get(url).json()['data'], item)
        self.assertEqual(self.client.post(self.assignment_url, json=self.assignment_body).status_code, 409)
        for suffix, total in (('', 1), ('&search=annual', 1), ('&search=Class', 1), ('&search=absent', 0), ('&search=%25', 0), ('&class_id=2', 0), ('&fee_structure_id=1', 1)):
            response = self.client.get(self.assignment_url + suffix)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['total'], total)
        response = self.client.put(url, json={'class_id': 1, 'fee_structure_id': 3})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['fee_structure_name'], 'Monthly')
        self.assertNotIn('admission_amount', response.json()['data'])
        self.assertEqual(self.client.delete('/schools/fee_structures/3?school_id=1&session_id=1').status_code, 409)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_scope_and_validation(self):
        for changes in ({'class_id': 2}, {'class_id': 3}, {'fee_structure_id': 2}, {'fee_structure_id': 999}):
            self.assertEqual(self.client.post(self.assignment_url, json=dict(self.assignment_body, **changes)).status_code, 404)
        for changes in ({'class_id': 0}, {'admission_amount': '-1'}, {'admission_amount': '1.001'}, {'admission_amount': 'NaN'}, {'unknown': 1}):
            self.assertEqual(self.client.post(self.assignment_url, json=dict(self.assignment_body, **changes)).status_code, 422)
        identity = self.client.post(self.assignment_url, json=self.assignment_body).json()['data']['id']
        for scope in ('school_id=2&session_id=2', 'school_id=1&session_id=3'):
            url = f'/schools/fee_assignments?{scope}&assignment_id={identity}'
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.put(url, json=self.assignment_body).status_code, 404)
            self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.delete(self.assignment_url).status_code, 422)
        self.assertEqual(self.client.get('/schools/fee_assignments').status_code, 422)
        self.assertEqual(self.db.query(FeeAssignment).count(), 1)

    def test_auto_creation_and_swagger(self):
        FeeAssignment.__table__.drop(self.engine)
        del self.client.app.dependency_overrides[get_db_session]
        with patch.object(database, 'engine', self.engine), patch.object(database, 'SessionLocal', lambda: database.sessionmaker(bind=self.engine)()):
            response = self.client.post(self.assignment_url, json=self.assignment_body)
        self.assertEqual(response.status_code, 201, response.text)
        schema = self.client.get('/openapi.json').json()
        methods = schema['paths']['/schools/fee_assignments']
        for method in ('get', 'post', 'put', 'delete'):
            params = {p['name']: p for p in methods[method]['parameters']}
            for name in ('school_id', 'session_id'):
                self.assertEqual(params[name]['in'], 'query')
                self.assertTrue(params[name]['required'])
        self.assertIn('examples', schema['components']['schemas']['FeeAssignmentWrite'])

    def test_existing_admission_column_is_removed(self):
        from sqlalchemy import inspect, text
        from database.table_init import ensure_all_tables
        item = self.client.post(self.assignment_url, json=self.assignment_body).json()['data']
        self.db.close()
        with self.engine.begin() as connection:
            connection.execute(text('ALTER TABLE fee_assignments ADD COLUMN admission_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (admission_amount >= 0)'))
            ensure_all_tables(connection)
            ensure_all_tables(connection)
            self.assertNotIn('admission_amount', {column['name'] for column in inspect(connection).get_columns('fee_assignments')})
        self.db.expire_all()
        self.assertEqual(self.client.get(self.assignment_url + f"&assignment_id={item['id']}").json()['data'], item)

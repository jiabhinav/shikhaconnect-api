import unittest

import catalog_setup
import test_sessions
from models.class_section import ClassSectionMapping


class ClassSectionMappingTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def create_catalog(self, resource, name, school=1, session=1):
        response = self.client.post(f'/schools/{resource}?school_id={school}&session_id={session}', json={'name': name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['data']['id']

    def test_mapping_crud_and_class_sections(self):
        class_id = self.create_catalog('classes', 'Class 1')
        section_id = self.create_catalog('sections', 'A')
        other_section = self.create_catalog('sections', 'B')
        url = '/schools/class-section-mappings?school_id=1&session_id=1'
        payload = {'class_id': class_id, 'section_id': section_id}
        created = self.client.post(url, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        mapping_id = created.json()['data']['id']
        detail = f'/schools/class-section-mappings?mapping_id={mapping_id}&school_id=1&session_id=1'
        self.assertEqual(self.client.get(detail).json()['data']['class_id'], class_id)
        self.assertEqual(self.client.post(url, json=payload).status_code, 409)
        sections_url = f'/schools/classes/sections?class_id={class_id}&school_id=1&session_id=1'
        self.assertEqual([s['id'] for s in self.client.get(sections_url).json()['data']], [section_id])
        updated = self.client.put(detail, json=dict(payload, section_id=other_section))
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual([s['id'] for s in self.client.get(sections_url).json()['data']], [other_section])
        self.assertEqual(len(self.client.get(url + f'&class_id={class_id}').json()['data']), 1)
        self.assertEqual(self.client.delete(detail).status_code, 200)
        self.assertEqual(self.client.get(sections_url).json()['data'], [])
        self.assertEqual(self.client.delete(detail).status_code, 404)

    def test_mapping_id_query_parameter(self):
        class_id = self.create_catalog('classes', 'Class 1')
        section_id = self.create_catalog('sections', 'A')
        other_section = self.create_catalog('sections', 'B')
        url = '/schools/class-section-mappings?school_id=1&session_id=1'
        payload = {'class_id': class_id, 'section_id': section_id}
        created = self.client.post(url, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        mapping_id = created.json()['data']['id']
        detail = url + f'&mapping_id={mapping_id}'
        paths = self.client.get('/openapi.json').json()['paths']
        self.assertNotIn('/schools/class-section-mappings/{mapping_id}', paths)
        self.assertNotIn('/schools/classes/{class_id}/sections', paths)
        section_parameters = paths['/schools/classes/sections']['get']['parameters']
        for name in ('class_id', 'school_id', 'session_id'):
            self.assertTrue(any(p['name'] == name and p['in'] == 'query' and p['required'] for p in section_parameters))
        self.assertEqual(self.client.get('/schools/classes/sections?school_id=1&session_id=1').status_code, 422)
        for method in ('get', 'put', 'delete'):
            parameters = paths['/schools/class-section-mappings'][method]['parameters']
            self.assertTrue(any(p['name'] == 'mapping_id' and p['in'] == 'query' for p in parameters))
        response = self.client.get(detail)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['id'], mapping_id)
        response = self.client.put(detail, json=dict(payload, section_id=other_section))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['section_id'], other_section)
        wrong_scope = f'/schools/class-section-mappings?mapping_id={mapping_id}&school_id=2&session_id=2'
        self.assertEqual(self.client.get(wrong_scope).status_code, 404)
        self.assertEqual(self.client.delete(wrong_scope).status_code, 404)
        self.assertEqual(self.client.delete(detail).status_code, 200)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.put(url, json=payload).status_code, 422)
        self.assertEqual(self.client.delete(url).status_code, 422)

    def test_scope_validation_and_initialization(self):
        class_id = self.create_catalog('classes', 'Class 1')
        section_id = self.create_catalog('sections', 'A')
        foreign_section = self.create_catalog('sections', 'B', school=2, session=2)
        next_section = self.create_catalog('sections', 'C', session=3)
        ClassSectionMapping.__table__.drop(self.engine)
        url = '/schools/class-section-mappings?school_id=1&session_id=1'
        for invalid_section in (foreign_section, next_section, 999):
            self.assertEqual(self.client.post(url, json={'class_id': class_id, 'section_id': invalid_section}).status_code, 404)
        self.assertEqual(self.client.post(url, json={'class_id': 0, 'section_id': section_id}).status_code, 422)
        created = self.client.post(url, json={'class_id': class_id, 'section_id': section_id})
        self.assertEqual(created.status_code, 201, created.text)
        mapping_id = created.json()['data']['id']
        wrong_scope = f'/schools/class-section-mappings?mapping_id={mapping_id}&school_id=2&session_id=2'
        self.assertEqual(self.client.get(wrong_scope).status_code, 404)
        self.assertEqual(self.client.delete(wrong_scope).status_code, 404)
        self.assertEqual(self.client.get(f'/schools/classes/sections?class_id={class_id}&school_id=1&session_id=3').status_code, 404)

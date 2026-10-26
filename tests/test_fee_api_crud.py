"""Exercise the documented query-scoped CRUD routes as a frontend would."""
import unittest

import catalog_setup
import test_sessions


class FeeApiCrudTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def test_catalog_crud_through_public_urls(self):
        resources = (
            ('fee_categories', {'name': 'General'}, {'name': 'Updated'}),
            ('fee_heads', {'fee_head_name': 'Tuition', 'student_type': 'All'},
             {'fee_head_name': 'Transport', 'student_type': 'New'}),
            ('fee_structures', {'fee_structure_name': 'Annual'}, {'fee_structure_name': 'Monthly'}),
        )
        for resource, create_body, update_body in resources:
            with self.subTest(resource=resource):
                scope = '?school_id=1&session_id=1'
                base = '/schools/' + resource
                response = self.client.post(base + scope, json=create_body)
                self.assertEqual(response.status_code, 201, response.text)
                item = response.json()['data']
                item_url = f"{base}/{item['id']}{scope}"
                self.assertEqual(self.client.get(item_url).json()['data'], item)
                self.assertIn(item, self.client.get(base + scope).json()['data'])
                response = self.client.put(item_url, json=update_body)
                self.assertEqual(response.status_code, 200, response.text)
                for field, value in update_body.items():
                    self.assertEqual(response.json()['data'][field], value)
                self.assertEqual(self.client.delete(item_url).status_code, 200)
                self.assertEqual(self.client.get(item_url).status_code, 404)
                self.assertEqual(self.client.get(base + scope).json()['data'], [])

    def test_all_crud_routes_are_in_openapi_with_required_scope(self):
        paths = self.client.get('/openapi.json').json()['paths']
        for resource, identity in (
            ('fee_categories', 'fee_category_id'), ('fee_heads', 'fee_head_id'),
            ('fee_structures', 'fee_structure_id'), ('fee_configurations', 'configuration_id'),
        ):
            base = '/schools/' + resource
            routes = ((base, ('post', 'get', 'put', 'delete')),) if resource == 'fee_configurations' else ((base, ('post', 'get')), (base + '/{' + identity + '}', ('get', 'put', 'delete')))
            for path, methods in routes:
                for method in methods:
                    with self.subTest(path=path, method=method):
                        operation = paths[path][method]
                        params = {param['name']: param for param in operation['parameters']}
                        for name in ('school_id', 'session_id'):
                            self.assertTrue(params[name]['required'])
                            self.assertEqual(params[name]['in'], 'query')

    def test_legacy_routes_stay_available_but_hidden(self):
        paths = self.client.get('/openapi.json').json()['paths']
        for resource in ('fee_categories', 'fee_heads', 'fee_structures', 'fee_configurations'):
            self.assertNotIn('/schools/{school_id}/sessions/{session_id}/' + resource, paths)
            for url in (f'/schools/1/sessions/1/{resource}',
                        f'/schools/school/1/sessions/1/{resource}'):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_month_routes_use_required_query_scope(self):
        paths = self.client.get('/openapi.json').json()['paths']
        for path, method in (
            ('/schools/fee_configurations/months', 'get'),
            ('/schools/fee_configurations/fee_heads/months', 'patch'),
        ):
            params = {param['name']: param for param in paths[path][method]['parameters']}
            for name in ('school_id', 'session_id'):
                self.assertTrue(params[name]['required'])
                self.assertEqual(params[name]['in'], 'query')

    def test_configuration_and_month_crud_with_query_scope(self):
        scope = '?school_id=1&session_id=1'
        def create(resource, body):
            response = self.client.post('/schools/' + resource + scope, json=body)
            self.assertEqual(response.status_code, 201, response.text)
            return response.json()['data']['id']

        structure = create('fee_structures', {'fee_structure_name': 'Annual'})
        category = create('fee_categories', {'name': 'General'})
        head = create('fee_heads', {'fee_head_name': 'Tuition', 'student_type': 'All'})
        body = {'fee_structure_id': structure, 'fee_category_id': category,
                'fee_heads': [{'fee_head_id': head, 'amount': '500'}]}
        identity = create('fee_configurations', body)
        base = f'/schools/fee_configurations/{identity}'
        self.assertEqual(self.client.get('/schools/fee_configurations' + scope).status_code, 200)
        self.assertEqual(self.client.get(base + scope).status_code, 200)
        self.assertEqual(self.client.put(base + scope, json=body).status_code, 200)
        schedule_url = '/schools/fee_configurations/months' + scope + f'&configuration_id={identity}'
        response = self.client.get(schedule_url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['configuration_id'], identity)
        self.assertEqual(self.client.get('/schools/fee_configurations/months' + scope).status_code, 422)
        self.assertEqual(self.client.get('/schools/fee_configurations/months?school_id=1&session_id=3' + f'&configuration_id={identity}').status_code, 404)
        params = self.client.get('/openapi.json').json()['paths']['/schools/fee_configurations/months']['get']['parameters']
        self.assertTrue(any(p['name'] == 'configuration_id' and p['in'] == 'query' and p['required'] for p in params))
        edit_url = f'/schools/fee_configurations/fee_heads/months?configuration_id={identity}&fee_head_id={head}'
        response = self.client.patch(edit_url + '&school_id=1&session_id=1', json={
            'month_amounts': {'2025-04': {'amount': '450', 'enabled': True}}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['monthly_totals']['2025-04'], '450.00')
        for url in (base, base + '/months'):
            self.assertEqual(self.client.get(url).status_code, 422)
            self.assertEqual(self.client.get(url + '?school_id=1&session_id=3').status_code, 404)
        self.assertEqual(self.client.patch(edit_url + '&school_id=1&session_id=3', json={'amount': '1'}).status_code, 404)
        self.assertEqual(self.client.delete(base + scope).status_code, 200)
        self.assertEqual(self.client.get(base + scope).status_code, 404)

    def test_month_edit_requires_all_query_ids(self):
        path = '/schools/fee_configurations/fee_heads/months'
        operation = self.client.get('/openapi.json').json()['paths'][path]['patch']
        params = {param['name']: param for param in operation['parameters']}
        values = dict(school_id=1, session_id=1, configuration_id=1, fee_head_id=1)
        for name in values:
            self.assertEqual(params[name]['in'], 'query')
            self.assertTrue(params[name]['required'])
            response = self.client.patch(path, params={key: value for key, value in values.items() if key != name}, json={'amount': '100'})
            self.assertEqual(response.status_code, 422, response.text)

    def test_configuration_id_query_for_get_update_delete(self):
        scope = '?school_id=1&session_id=1'
        def create(resource, body):
            return self.client.post('/schools/' + resource + scope, json=body).json()['data']['id']
        structure = create('fee_structures', {'fee_structure_name': 'Annual'})
        category = create('fee_categories', {'name': 'General'})
        head = create('fee_heads', {'fee_head_name': 'Tuition', 'student_type': 'All'})
        body = {'fee_structure_id': structure, 'fee_category_id': category,
                'fee_heads': [{'fee_head_id': head, 'amount': '500'}]}
        identity = create('fee_configurations', body)
        url = '/schools/fee_configurations' + scope + f'&configuration_id={identity}'
        self.assertEqual(self.client.get(url).json()['data']['id'], identity)
        self.assertIsInstance(self.client.get('/schools/fee_configurations' + scope).json()['data'], list)
        body['fee_heads'][0]['amount'] = '600'
        response = self.client.put(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['fee_heads'][0]['amount'], '600.00')
        self.assertEqual(self.client.put('/schools/fee_configurations' + scope, json=body).status_code, 422)
        self.assertEqual(self.client.delete('/schools/fee_configurations' + scope).status_code, 422)
        self.assertEqual(self.client.get(url.replace('session_id=1', 'session_id=3')).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 404)

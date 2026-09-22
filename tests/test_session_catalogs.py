import unittest
import catalog_setup
import test_sessions


class SessionCatalogTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def test_all_catalogs_are_isolated_for_create_list_update_delete(self):
        for resource in ('caste_categories', 'classes', 'sections', 'fee_categories',
                         'houses', 'streams', 'subjects'):
            with self.subTest(resource=resource):
                first = f'/schools/school/1/sessions/1/{resource}'
                second = f'/schools/school/1/sessions/3/{resource}'
                payload = {'name': 'Shared'}
                if resource == 'subjects':
                    payload['code'] = 'CODE'
                response = self.client.post(first, json=payload)
                self.assertEqual(response.status_code, 201, response.text)
                item = response.json()['data']
                self.assertEqual(item['session_id'], 1)
                self.assertEqual(self.client.get(second).json()['data'], [])
                response = self.client.post(second, json=payload)
                self.assertEqual(response.status_code, 201, response.text)
                other = response.json()['data']
                self.assertEqual(other['session_id'], 3)
                if resource == 'classes':
                    self.assertEqual((item['class_order'], other['class_order']), (1, 1))
                self.assertEqual(self.client.post(first, json=payload).status_code, 409)
                wrong_item = f"{second}/{item['id']}"
                self.assertEqual(self.client.put(wrong_item, json={'name': 'Wrong'}).status_code, 404)
                self.assertEqual(self.client.delete(wrong_item).status_code, 404)
                wrong_school = f'/schools/school/2/sessions/1/{resource}'
                self.assertEqual(self.client.get(wrong_school).status_code, 404)
                self.assertEqual(self.client.post(wrong_school, json=payload).status_code, 404)
                self.assertEqual(self.client.get(first.replace('/sessions/1/', '/sessions/999/')).status_code, 404)
                if resource in ('caste_categories', 'fee_categories', 'houses'):
                    self.assertEqual(self.client.get(wrong_item).status_code, 404)
                if resource == 'subjects':
                    self.assertEqual(self.client.patch(wrong_item + '/status', json={'status': 'Inactive'}).status_code, 404)
                own_item = f"{first}/{item['id']}"
                self.assertEqual(self.client.put(own_item, json={'name': 'Updated'}).status_code, 200)
                self.assertEqual(self.client.delete(own_item).status_code, 200)
                self.assertEqual([row['id'] for row in self.client.get(second).json()['data']], [other['id']])

    def test_student_cannot_use_catalog_from_another_session(self):
        from routers.students import validate_student_references
        from fastapi import HTTPException
        from types import SimpleNamespace
        ids = {}
        for field, resource in (('class_id', 'classes'), ('fee_category_id', 'fee_categories'),
                                ('caste_category_id', 'caste_categories')):
            ids[field] = self.client.post(f'/schools/school/1/sessions/1/{resource}',
                                         json={'name': 'Test'}).json()['data']['id']
        validate_student_references(self.db, 1, SimpleNamespace(student_info=SimpleNamespace(session_id=1, **ids)))
        with self.assertRaises(HTTPException) as error:
            validate_student_references(self.db, 1, SimpleNamespace(student_info=SimpleNamespace(session_id=3, **ids)))
        self.assertEqual(error.exception.status_code, 404)

    def test_timetable_and_generation_settings_keep_separate_session_values(self):
        settings = {
            'timetable-settings': {
                'summer_start_time': '08:00:00', 'summer_end_time': '14:00:00',
                'winter_start_time': '09:00:00', 'winter_end_time': '15:00:00',
                'minimum_attendance_percentage': 75, 'term_attendance_enabled': True,
            },
            'fee-generation-settings': {'generation_day': 1, 'payment_due_days': 10},
            'transport-generation-settings': {'generation_day': 1, 'payment_due_day': 10},
        }
        for resource, payload in settings.items():
            first = f'/schools/school/1/sessions/1/{resource}'
            second = f'/schools/school/1/sessions/3/{resource}'
            response = self.client.put(first, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data']['session_id'], 1)
            self.assertEqual(self.client.get(second).status_code, 404)
            updated = dict(payload)
            updated['minimum_attendance_percentage' if resource == 'timetable-settings' else 'generation_day'] = 5
            response = self.client.put(second, json=updated)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data']['session_id'], 3)
            field = 'minimum_attendance_percentage' if resource == 'timetable-settings' else 'generation_day'
            self.assertEqual(float(self.client.get(first).json()['data'][field]), payload[field])
            self.assertEqual(self.client.put(first.replace('/school/1/', '/school/2/'), json=payload).status_code, 404)

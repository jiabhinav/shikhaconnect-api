import unittest

import test_sessions


class SessionOverlapTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def test_overlaps_and_school_scope(self):
        url = '/schools/sessions?school_id=1'
        response = self.client.post(url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        session_id = response.json()['data']['id']
        for start, end in (
            ('2025-04-01', '2026-03-31'),
            ('2025-06-01', '2025-07-01'),
            ('2024-01-01', '2027-01-01'),
            ('2024-01-01', '2025-04-01'),
            ('2026-03-31', '2027-01-01'),
        ):
            with self.subTest(start=start, end=end):
                payload = dict(self.payload, start_date=start, end_date=end)
                self.assertEqual(self.client.post(url, json=payload).status_code, 409)
        self.assertEqual(self.client.post('/schools/sessions?school_id=2', json=self.payload).status_code, 201)
        self.assertEqual(self.client.put(f'{url}&session_id={session_id}', json=self.payload).status_code, 200)

    def test_nonoverlapping_dates_in_same_year_are_allowed(self):
        url = '/schools/sessions?school_id=1'
        for start, end in (('2026-01-01', '2026-03-31'), ('2026-04-01', '2026-12-31')):
            response = self.client.post(url, json=dict(self.payload, start_date=start, end_date=end))
            self.assertEqual(response.status_code, 201, response.text)

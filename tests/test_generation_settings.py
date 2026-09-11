import unittest
import test_sessions
from models.generation_settings import FeeGenerationSettings, TransportGenerationSettings
from models.school import SchoolUserAssignment
from models.user import UserRole
from database.table_init import ensure_all_tables


class GenerationSettingsTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def session_id(self):
        return self.client.post('/schools/school/1/sessions', json=self.payload).json()['data']['id']

    def test_save_get_update_independent_panels(self):
        sid = self.session_id()
        for endpoint, due in [('fee-generation-settings', 'payment_due_days'), ('transport-generation-settings', 'payment_due_day')]:
            url = f'/schools/school/1/sessions/{sid}/{endpoint}'
            self.assertEqual(self.client.get(url).status_code, 404)
            payload = {'generation_day': 1, due: 10, 'late_fee_enabled': True}
            response = self.client.put(url, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()['data']
            self.assertEqual(self.client.get(url).json()['data'], item)
            response = self.client.put(url, json=dict(payload, generation_day=5, late_fee_enabled=False))
            self.assertEqual(response.json()['data']['id'], item['id'])
            self.assertFalse(response.json()['data']['late_fee_enabled'])
        self.assertEqual(self.db.query(FeeGenerationSettings).count(), 1)
        self.assertEqual(self.db.query(TransportGenerationSettings).count(), 1)

    def test_access_and_validation(self):
        sid = self.session_id()
        payload = {'generation_day': 1, 'payment_due_days': 5}
        url = f'/schools/school/1/sessions/{sid}/fee-generation-settings'
        other = f'/schools/school/2/sessions/{sid}/fee-generation-settings'
        self.assertEqual(self.client.put(other, json=payload).status_code, 404)
        for values in ({'generation_day':0}, {'generation_day':32}, {'payment_due_days':-1}, {'generation_day':True}):
            self.assertEqual(self.client.put(url, json=dict(payload, **values)).status_code, 422)
        transport = f'/schools/school/1/sessions/{sid}/transport-generation-settings'
        self.assertEqual(self.client.put(transport, json={'generation_day':1,'payment_due_day':32}).status_code, 422)
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.put(url, json=payload).status_code, 404)
            self.assertEqual(self.client.get(url).status_code, 404)
            assignment = SchoolUserAssignment(school_id=1,user_id=1,role=role.value)
            self.db.add(assignment)
            self.db.commit()
            self.assertEqual(self.client.put(url,json=payload).status_code, 200)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.db.delete(assignment)
            self.db.commit()

    def test_missing_tables_recreated(self):
        for model in (FeeGenerationSettings, TransportGenerationSettings):
            model.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            ensure_all_tables(connection)
        self.assertEqual(self.db.query(FeeGenerationSettings).count(), 0)
        self.assertEqual(self.db.query(TransportGenerationSettings).count(), 0)

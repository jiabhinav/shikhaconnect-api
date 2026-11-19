import unittest
from datetime import date
import test_sessions
from models.generation_settings import FeeGenerationSettings, TransportGenerationSettings
from models.user import UserRole
from database.table_init import ensure_all_tables


class GenerationSettingsTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def session_id(self):
        return self.client.post('/schools/school/1/sessions', json=self.payload).json()['data']['id']

    def test_save_get_update_independent_panels(self):
        sid = self.session_id()
        for endpoint, due, fee_type in (
            ('fee-generation-settings', 'payment_due_days', 'school_fee'),
            ('transport-generation-settings', 'payment_due_day', 'transport'),
        ):
            url = f'/schools/school/1/sessions/{sid}/{endpoint}'
            self.assertEqual(self.client.get(url).status_code, 404)
            payload = {
                'school_id': 1, 'generation_day': 1, due: 10,
                'late_fee_enabled': True, 'fee_type': fee_type,
                'late_fee_amount': '125.50', 'min_due_amount': 1, 'late_fee_type': 'daily',
            }
            for changes in (
                {'late_fee_amount': None}, {'late_fee_amount': 0}, {'late_fee_amount': '1.234'},
                {'min_due_amount': None}, {'min_due_amount': 0}, {'min_due_amount': 101},
                {'late_fee_type': 'weekly'},
                {'fee_type': 'transport' if fee_type == 'school_fee' else 'school_fee'},
            ):
                self.assertEqual(self.client.put(url, json=payload | changes).status_code, 422)
            self.assertEqual(self.client.post(url, json=dict(payload, school_id=2)).status_code, 422)
            self.assertEqual(self.client.put(url, json=payload).status_code, 404)
            response = self.client.post(url, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(self.client.post(url, json=payload).status_code, 409)
            self.assertEqual(self.client.put(url, json=dict(payload, school_id=2)).status_code, 422)
            item = response.json()['data']
            self.assertEqual(item['late_fee_amount'], '125.50')
            self.assertEqual(item['min_due_amount'], 1)
            self.assertEqual(item['late_fee_type'], 'daily')
            self.assertEqual(item['fee_type'], fee_type)
            self.assertEqual(item['school_id'], 1)
            for field in ('created_at', 'updated_at'):
                self.assertEqual(date.fromisoformat(item[field]).isoformat(), item[field])
            self.assertEqual(self.client.get(url).json()['data'], item)
            response = self.client.put(url, json=dict(payload, min_due_amount=100, late_fee_type='monthly'))
            self.assertEqual(response.json()['data']['min_due_amount'], 100)
            self.assertEqual(self.client.get(url).json()['data']['late_fee_type'], 'monthly')
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
        self.assertEqual(self.client.post(other, json=payload).status_code, 404)
        for values in ({'generation_day':0}, {'generation_day':32}, {'payment_due_days':-1}, {'generation_day':True}):
            self.assertEqual(self.client.put(url, json=dict(payload, **values)).status_code, 422)
        transport = f'/schools/school/1/sessions/{sid}/transport-generation-settings'
        self.assertEqual(self.client.put(transport, json={'generation_day':1,'payment_due_day':32}).status_code, 422)
        self.assertEqual(self.client.post(url, json=payload).status_code, 201)
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.put(url, json=payload).status_code, 403)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_missing_tables_recreated(self):
        for model in (FeeGenerationSettings, TransportGenerationSettings):
            model.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            ensure_all_tables(connection)
        self.assertEqual(self.db.query(FeeGenerationSettings).count(), 0)
        self.assertEqual(self.db.query(TransportGenerationSettings).count(), 0)

    def test_startup_adds_columns_to_existing_settings(self):
        sid = self.session_id()
        resources = (
            (FeeGenerationSettings, 'fee-generation-settings', 'payment_due_days'),
            (TransportGenerationSettings, 'transport-generation-settings', 'payment_due_day'),
        )
        with self.engine.begin() as connection:
            for model, _, due in resources:
                model.__table__.drop(connection)
                table = model.__tablename__
                connection.exec_driver_sql(
                    f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, session_id INTEGER UNIQUE, "
                    f"generation_day INTEGER, {due} INTEGER, late_fee_enabled BOOLEAN)"
                )
                connection.exec_driver_sql(
                    f"INSERT INTO {table} VALUES (1, ?, 1, 10, true)", (sid,)
                )
            ensure_all_tables(connection)
            ensure_all_tables(connection)

        for _, endpoint, _ in resources:
            url = f'/schools/school/1/sessions/{sid}/{endpoint}'
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()['data']
            self.assertTrue(data['late_fee_enabled'])
            self.assertEqual(data['school_id'], 1)
            self.assertIsNone(data['late_fee_amount'])
            self.assertIsNone(data['min_due_amount'])
            self.assertIsNone(data['late_fee_type'])
            self.assertIsNone(data['created_at'])
            self.assertIsNone(data['updated_at'])

import unittest
from datetime import date
import test_sessions
from sqlalchemy import inspect

from models.generation_settings import FeeGenerationSettings
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
            params = {'fee_type': fee_type}
            empty = self.client.get(url, params=params)
            self.assertEqual(empty.status_code, 200)
            self.assertEqual(empty.json()['data'], {'school_fee': None, 'transport': None})
            payload = {
                'school_id': 1, 'generation_day': 1, due: 10,
                'late_fee_enabled': True, 'fee_type': fee_type,
                'late_fee_amount': '125.50', 'min_due_amount': 1, 'late_fee_type': 'daily',
            }
            for changes in (
                {'late_fee_amount': None}, {'late_fee_amount': 0}, {'late_fee_amount': '1.234'},
                {'min_due_amount': None}, {'min_due_amount': 0}, {'min_due_amount': 101},
                {'late_fee_type': 'weekly'},
                {'fee_type': 'other'},
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
            fetched = self.client.get(url, params=params).json()['data']
            expected = dict.fromkeys(('school_fee', 'transport'))
            expected[fee_type] = item
            self.assertEqual(fetched, expected)
            response = self.client.put(url, json=dict(payload, min_due_amount=100, late_fee_type='monthly'))
            self.assertEqual(response.json()['data']['min_due_amount'], 100)
            fetched = self.client.get(url).json()['data']
            self.assertEqual(fetched[fee_type]['late_fee_type'], 'monthly')
            response = self.client.put(url, json=dict(payload, generation_day=5, late_fee_enabled=False))
            self.assertEqual(response.json()['data']['id'], item['id'])
            self.assertFalse(response.json()['data']['late_fee_enabled'])
        combined = self.client.get(f'/schools/fee-generation-settings?school_id=1&session_id={sid}')
        self.assertEqual(combined.status_code, 200, combined.text)
        data = combined.json()['data']
        self.assertEqual(data['school_fee']['fee_type'], 'school_fee')
        self.assertEqual(data['transport']['fee_type'], 'transport')
        self.assertEqual(self.db.query(FeeGenerationSettings).count(), 2)

    def test_explicit_fee_type_selects_settings_on_either_endpoint(self):
        sid = self.session_id()
        for endpoint, fee_type in (
            ('transport-generation-settings', 'school_fee'),
            ('fee-generation-settings', 'transport'),
        ):
            url = f'/schools/{endpoint}'
            params = {'school_id': 1, 'session_id': sid}
            alternate = 'payment_due_day' if fee_type == 'school_fee' else 'payment_due_days'
            payload = {'fee_type': fee_type, 'generation_day': 1}
            response = self.client.post(url, params=params, json=dict(payload, **{alternate: 10}))
            if fee_type == 'transport':
                invalid = self.client.put(url, params=params, json=dict(payload, payment_due_days=32))
                self.assertEqual(invalid.status_code, 422)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()['data']['fee_type'], fee_type)
            self.assertTrue(response.json()['data']['late_fee_enabled'])
            self.assertEqual(response.json()['data']['payment_due_day'], 10)
            response = self.client.put(url, params=params, json=dict(payload, **{alternate: 15}))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data']['payment_due_day'], 15)
            self.assertTrue(response.json()['data']['late_fee_enabled'])
            fetched = self.client.get(url, params=dict(params, fee_type=fee_type))
            self.assertEqual(fetched.status_code, 200, fetched.text)
            data = response.json()['data']
            expected = dict.fromkeys(('school_fee', 'transport'))
            expected[fee_type] = data
            self.assertEqual(fetched.json()['data'], expected)

    def test_same_endpoint_creates_one_record_per_fee_type(self):
        url = '/schools/fee-generation-settings'
        params = {'school_id': 1, 'session_id': self.session_id()}
        payload = {'generation_day': 3, 'payment_due_day': 10}
        created = {}
        for fee_type in ('school_fee', 'transport'):
            body = dict(payload, fee_type=fee_type)
            response = self.client.post(url, params=params, json=body)
            self.assertEqual(response.status_code, 201, response.text)
            created[fee_type] = response.json()['data']

            duplicate = self.client.post(url, params=params, json=dict(body, generation_day=4))
            self.assertEqual(duplicate.status_code, 409, duplicate.text)
            self.assertIn(fee_type, duplicate.json()['detail'])

        response = self.client.get(url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], created)
        transport = self.client.get('/schools/transport-generation-settings', params=params)
        self.assertEqual(transport.status_code, 200, transport.text)
        self.assertEqual(transport.json()['data'], created)

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
        created = self.client.post(url, json=payload)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()['data']['fee_type'], 'school_fee')
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.put(url, json=payload).status_code, 403)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_missing_table_recreated(self):
        FeeGenerationSettings.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            ensure_all_tables(connection)
            self.assertFalse(inspect(connection).has_table('transport_generation_settings'))
        self.assertEqual(self.db.query(FeeGenerationSettings).count(), 0)

    def test_startup_merges_existing_settings(self):
        sid = self.session_id()
        with self.engine.begin() as connection:
            FeeGenerationSettings.__table__.drop(connection)
            for table, due_field, due in (
                ('fee_generation_settings', 'payment_due_days', 10),
                ('transport_generation_settings', 'payment_due_day', 15),
            ):
                connection.exec_driver_sql(
                    f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, session_id INTEGER UNIQUE, "
                    f"generation_day INTEGER, {due_field} INTEGER, late_fee_enabled BOOLEAN, "
                    "late_fee_amount NUMERIC(12, 2), min_due_amount INTEGER, late_fee_type VARCHAR(7))"
                )
                connection.exec_driver_sql(
                    f"INSERT INTO {table} VALUES (8, ?, 1, ?, true, 125.50, 25, 'monthly')", (sid, due)
                )
            ensure_all_tables(connection)
            ensure_all_tables(connection)
            self.assertFalse(inspect(connection).has_table("transport_generation_settings"))
            stored = connection.exec_driver_sql(
                "SELECT fee_type, payment_due_day "
                "FROM fee_generation_settings ORDER BY fee_type"
            ).all()
            self.assertEqual(stored, [('school_fee', 10), ('transport', 15)])

        url = f'/schools/school/1/sessions/{sid}/fee-generation-settings'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()['data']
        school, transport = data['school_fee'], data['transport']
        self.assertEqual(school['id'], 8)
        self.assertNotEqual(transport['id'], school['id'])
        for item in (school, transport):
            self.assertTrue(item['late_fee_enabled'])
            self.assertEqual(item['school_id'], 1)
            self.assertEqual(item['late_fee_amount'], '125.50')
            self.assertEqual(item['min_due_amount'], 25)
            self.assertEqual(item['late_fee_type'], 'monthly')
            self.assertIsNone(item['created_at'])
            self.assertIsNone(item['updated_at'])

    def test_startup_drops_legacy_transport_table_after_prior_merge(self):
        sid = self.session_id()
        session = self.client.post(
            "/schools/school/1/sessions",
            json={"name": "2027-2028", "start_date": "2027-04-01", "end_date": "2028-03-31"},
        )
        self.assertEqual(session.status_code, 201, session.text)
        missing_sid = session.json()["data"]["id"]
        response = self.client.post(
            "/schools/transport-generation-settings",
            params={"school_id": 1, "session_id": sid},
            json={"generation_day": 3, "payment_due_day": 20},
        )
        self.assertEqual(response.status_code, 201, response.text)

        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE transport_generation_settings (id INTEGER PRIMARY KEY, "
                "session_id INTEGER UNIQUE, generation_day INTEGER, payment_due_day INTEGER, late_fee_enabled BOOLEAN)"
            )
            connection.exec_driver_sql(
                "INSERT INTO transport_generation_settings VALUES (1, ?, 3, 10, false), (2, ?, 3, 15, false)",
                (sid, missing_sid),
            )
            ensure_all_tables(connection)
            ensure_all_tables(connection)
            self.assertFalse(inspect(connection).has_table("transport_generation_settings"))
            rows = connection.exec_driver_sql(
                "SELECT session_id, payment_due_day FROM fee_generation_settings "
                "WHERE fee_type = 'transport' ORDER BY session_id"
            ).all()
            self.assertEqual(rows, [(sid, 20), (missing_sid, 15)])

    def test_startup_replaces_two_due_columns_with_one(self):
        sid = self.session_id()
        with self.engine.begin() as connection:
            FeeGenerationSettings.__table__.drop(connection)
            connection.exec_driver_sql(
                "CREATE TABLE fee_generation_settings (id INTEGER PRIMARY KEY, session_id INTEGER, "
                "fee_type VARCHAR(10), generation_day INTEGER, late_fee_enabled BOOLEAN, "
                "payment_due_days INTEGER, payment_due_day INTEGER, UNIQUE (session_id, fee_type))"
            )
            connection.exec_driver_sql(
                "INSERT INTO fee_generation_settings VALUES "
                "(1, ?, 'transport', 1, false, NULL, 11), (3, ?, 'school_fee', 2, false, 6, NULL)",
                (sid, sid),
            )
            ensure_all_tables(connection)
            ensure_all_tables(connection)
            columns = {column["name"] for column in inspect(connection).get_columns("fee_generation_settings")}
            self.assertNotIn("payment_due_days", columns)
            rows = connection.exec_driver_sql(
                "SELECT id, fee_type, payment_due_day FROM fee_generation_settings ORDER BY id"
            ).all()
            self.assertEqual(rows, [(1, "transport", 11), (3, "school_fee", 6)])

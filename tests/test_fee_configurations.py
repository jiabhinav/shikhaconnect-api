import copy
import unittest
from unittest.mock import patch

import catalog_setup
import test_sessions
from database import database
from dependencies.db import get_db_session
from models.fee_configuration import FeeConfiguration, FeeConfigurationHead


class FeeConfigurationTests(unittest.TestCase):
    tearDown = test_sessions.SessionTests.tearDown

    def setUp(self):
        catalog_setup.setUp(self)
        self.config_url = '/schools/1/sessions/1/fee_configurations'
        self.structure = self.client.post('/schools/fee_structures?school_id=1&session_id=1', json={'fee_structure_name': 'Annual'}).json()['data']['id']
        self.category = self.client.post('/schools/fee_categories?school_id=1&session_id=1', json={'name': 'General'}).json()['data']['id']
        self.heads = [self.client.post('/schools/fee_heads?school_id=1&session_id=1', json={'fee_head_name': name, 'student_type': 'All'}).json()['data']['id'] for name in ('Tuition', 'Transport')]
        self.config_body = {'fee_structure_id': self.structure, 'fee_category_id': self.category, 'no_of_fee_heads': 2, 'fee_heads': [
            {'fee_head_id': self.heads[0], 'amount': '500.50'},
            {'fee_head_id': self.heads[1], 'amount': '200', 'edit_months_wise': True, 'month_amounts': {'2025-04': '150.25', '2025-05': '0'}},
        ]}

        defaults = {f'2025-{month:02}': '200' for month in range(1, 13)}
        defaults.update(self.config_body['fee_heads'][1]['month_amounts'])
        self.config_body['fee_heads'][1]['month_amounts'] = defaults

    def test_crud_and_replacement(self):
        response = self.client.post(self.config_url, json=self.config_body)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual(item['no_of_fee_heads'], 2)
        self.assertEqual(item['fee_heads'][0]['april_fee'], '500.50')
        self.assertEqual(item['fee_heads'][1]['april_fee'], '150.25')
        self.assertEqual(item['fee_heads'][1]['may_fee'], '0.00')
        self.assertEqual(item['fee_heads'][1]['month_amounts']['2025-04'], '150.25')
        self.assertEqual(self.client.post(self.config_url, json=self.config_body).status_code, 409)
        url = f"/schools/fee_configurations/{item['id']}?school_id=1&session_id=1"
        self.assertEqual(self.client.get(url).json()['data'], item)
        body = copy.deepcopy(self.config_body)
        body['fee_heads'] = [dict(body['fee_heads'][0], amount='700.75')]
        body['no_of_fee_heads'] = 1
        response = self.client.put(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['fee_heads'][0]['amount'], '700.75')
        self.assertEqual(self.db.query(FeeConfigurationHead).count(), 1)
        self.assertEqual(len(self.client.get(self.config_url).json()['data']), 1)
        self.assertEqual(self.client.get(self.config_url + '?fee_structure_id=999').json()['data'], [])
        self.assertEqual(self.client.delete(f'/schools/fee_heads/{self.heads[0]}?school_id=1&session_id=1').status_code, 409)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.db.query(FeeConfigurationHead).count(), 0)
        self.assertEqual(self.client.delete(url).status_code, 404)

    def test_scope_and_validation(self):
        for field in ('fee_structure_id', 'fee_category_id'):
            body = dict(self.config_body, **{field: 999})
            self.assertEqual(self.client.post(self.config_url, json=body).status_code, 404)
        self.assertEqual(self.client.post('/schools/fee_configurations?school_id=2&session_id=2', json=self.config_body).status_code, 404)
        response = self.client.post(self.config_url, json=self.config_body)
        identity = response.json()['data']['id']
        for method in ('get', 'put', 'delete'):
            kwargs = {'json': self.config_body} if method == 'put' else {}
            self.assertEqual(getattr(self.client, method)(f'/schools/fee_configurations/{identity}?school_id=1&session_id=3', **kwargs).status_code, 404)
        invalid = [dict(self.config_body, no_of_fee_heads=3), dict(self.config_body, fee_heads=[]), dict(self.config_body, fee_heads=[self.config_body['fee_heads'][0]] * 2)]
        for changes in ({'amount': '-1'}, {'amount': '1.001'}, {'amount': 'NaN'}, {'fee_head_id': 999}, {'edit_months_wise': True}, {'month_amounts': {'2025-13': '2'}, 'edit_months_wise': True}, {'month_amounts': {'2026-01': '2'}, 'edit_months_wise': True}):
            body = copy.deepcopy(self.config_body)
            body['fee_heads'][0].update(changes)
            invalid.append(body)
        for body in invalid:
            expected = 404 if body['fee_heads'] and body['fee_heads'][0]['fee_head_id'] == 999 else 422
            self.assertEqual(self.client.post(self.config_url, json=body).status_code, expected, body)
        self.assertEqual(self.db.query(FeeConfiguration).count(), 1)
        self.assertEqual(self.client.get('/schools/school/1/sessions/1/fee_configurations').status_code, 200)

    def test_failed_update_preserves_configuration(self):
        self.assertEqual(self.client.post(self.config_url, json=self.config_body).status_code, 201)
        category = self.client.post('/schools/fee_categories?school_id=1&session_id=1', json={'name': 'Other'}).json()['data']['id']
        body = dict(self.config_body, fee_category_id=category)
        second = self.client.post(self.config_url, json=body).json()['data']
        url = f"/schools/fee_configurations/{second['id']}?school_id=1&session_id=1"
        self.assertEqual(self.client.put(url, json=self.config_body).status_code, 409)
        self.assertEqual(self.client.get(url).json()['data'], second)
        self.assertEqual(self.db.query(FeeConfigurationHead).count(), 4)
        # A head from another session must be rejected independently of the structure/category.
        head = self.client.post('/schools/fee_heads?school_id=1&session_id=3', json={'fee_head_name': 'Other session', 'student_type': 'All'}).json()['data']['id']
        body = copy.deepcopy(self.config_body)
        body['fee_heads'][0]['fee_head_id'] = head
        self.assertEqual(self.client.put(url, json=body).status_code, 404)
        self.assertEqual(self.client.get(url).json()['data'], second)

    def test_month_checkboxes_round_trip_and_update(self):
        body = copy.deepcopy(self.config_body)
        months = {
            f"2025-{month:02}": {"amount": "500.25", "enabled": month != 6}
            for month in range(1, 13)
        }
        body['fee_heads'][0].update(edit_months_wise=True, month_amounts=months)
        response = self.client.post(self.config_url, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual(item['fee_heads'][0]['month_amounts'], {key: dict(value, amount=None) if not value['enabled'] else value for key, value in months.items()})
        self.assertEqual(item['fee_heads'][0]['june_fee'], '0.00')
        self.assertEqual(item['fee_heads'][0]['april_fee'], '500.25')
        self.db.expire_all()
        url = f"/schools/fee_configurations/{item['id']}?school_id=1&session_id=1"
        self.assertEqual(self.client.get(url).json()['data']['fee_heads'][0]['month_amounts'], {key: dict(value, amount=None) if not value['enabled'] else value for key, value in months.items()})
        # All months can be unchecked while keeping month-wise editing enabled.
        for settings in months.values():
            settings['enabled'] = False
        response = self.client.put(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['data']['fee_heads'][0]['edit_months_wise'])
        self.assertTrue(all(value['amount'] is None and not value['enabled'] for value in response.json()['data']['fee_heads'][0]['month_amounts'].values()))
        self.assertTrue(all(value['amount'] == '500.25' for value in self.db.query(FeeConfigurationHead).filter_by(fee_head_id=self.heads[0]).one().month_amounts.values()))
        body['fee_heads'][0].update(edit_months_wise=False, month_amounts={})
        response = self.client.put(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['data']['fee_heads'][0]['edit_months_wise'])
        self.assertEqual(len(response.json()['data']['fee_heads'][0]['month_amounts']), 12)
        self.assertTrue(all(value == '500.50' for value in response.json()['data']['fee_heads'][0]['month_amounts'].values()))

    def test_month_checkbox_validation(self):
        for settings in (
            {'amount': '-1', 'enabled': False},
            {'amount': '1.001', 'enabled': True},
            {'amount': 'NaN', 'enabled': True},
            {'amount': '100', 'enabled': 'false'},
            {'amount': '100'},
            {'enabled': False},
            {'amount': '100', 'enabled': True, 'unknown': 1},
        ):
            body = copy.deepcopy(self.config_body)
            body['fee_heads'][0].update(edit_months_wise=True, month_amounts={'2025-04': settings})
            response = self.client.post(self.config_url, json=body)
            self.assertEqual(response.status_code, 422, response.text)
        body = copy.deepcopy(self.config_body)
        body['fee_heads'][0].update(edit_months_wise=True, month_amounts={'2026-01': {'amount': '500', 'enabled': False}})
        self.assertEqual(self.client.post(self.config_url, json=body).status_code, 422)
        self.assertEqual(self.db.query(FeeConfiguration).count(), 0)

    def test_month_column_migration_backfills_existing_rows(self):
        from sqlalchemy import text
        from database.fee_configuration_table import ensure_fee_month_columns
        from utils.fee_months import MONTH_COLUMNS

        self.assertEqual(self.client.post(self.config_url, json=self.config_body).status_code, 201)
        self.db.close()
        with self.engine.begin() as connection:
            for name in MONTH_COLUMNS.values():
                connection.execute(text(f'ALTER TABLE fee_configuration_heads DROP COLUMN {name}'))
            ensure_fee_month_columns(connection)
            ensure_fee_month_columns(connection)
        self.db.expire_all()
        rows = self.db.query(FeeConfigurationHead).order_by(FeeConfigurationHead.id).all()
        self.assertEqual(str(rows[0].april_fee), '500.50')
        self.assertEqual(str(rows[1].april_fee), '150.25')
        self.assertEqual(str(rows[1].may_fee), '0.00')
        self.assertEqual(str(rows[1].january_fee), '200.00')

    def test_month_management_and_totals(self):
        from decimal import Decimal
        response = self.client.post(self.config_url, json=self.config_body)
        item = response.json()['data']
        base = f"/schools/1/sessions/1/fee_configurations/{item['id']}"
        scope = ''
        url = base + f"/fee_heads/{self.heads[0]}/months" + scope
        schedule = self.client.get(base + '/months' + scope).json()['data']
        self.assertEqual(len(schedule['fee_heads'][0]['months']), 12)
        self.assertEqual(schedule['total'], '8156.25')
        response = self.client.patch(url, json={'amount': '600', 'month_amounts': {
            '2025-04': {'amount': '500.25', 'enabled': False},
            '2025-06': {'amount': '300.50', 'enabled': True},
        }})
        self.assertEqual(response.status_code, 200, response.text)
        schedule = response.json()['data']
        head = schedule['fee_heads'][0]
        april = next(row for row in head['months'] if row['month'] == '2025-04')
        self.assertIsNone(april['amount'])
        self.assertEqual(Decimal(april['effective_amount']), 0)
        self.assertTrue(april['is_override'])
        self.assertEqual(Decimal(head['total']), Decimal('6300.50'))
        self.assertEqual(Decimal(schedule['monthly_totals']['2025-04']), Decimal('150.25'))
        saved = self.client.get(base + scope).json()['data']
        self.assertEqual(saved['fee_heads'][0]['id'], item['fee_heads'][0]['id'])
        self.assertEqual(saved['fee_heads'][1], item['fee_heads'][1])
        self.assertEqual(saved['fee_heads'][0]['april_fee'], '0.00')
        self.assertEqual(saved['fee_heads'][0]['june_fee'], '300.50')
        # Only reset April. June's override remains untouched.
        response = self.client.patch(url, json={'month_amounts': {'2025-04': None}})
        self.assertEqual(response.status_code, 200, response.text)
        april = response.json()['data']['fee_heads'][0]['months'][3]
        self.assertFalse(april['is_override'])
        self.assertTrue(april['enabled'])
        self.assertEqual(Decimal(april['effective_amount']), 600)
        saved = self.client.get(base + scope).json()['data']['fee_heads'][0]
        self.assertEqual(saved['june_fee'], '300.50')
        self.assertEqual(saved['april_fee'], '600.00')

    def test_month_management_scope_validation_and_session_order(self):
        from datetime import date
        from models.session import Session as SchoolSession
        session = self.db.query(SchoolSession).filter_by(id=1).one()
        session.start_date, session.end_date = date(2025, 4, 1), date(2026, 3, 31)
        overrides = self.config_body['fee_heads'][1]['month_amounts']
        for month in range(1, 4):
            overrides[f'2026-{month:02}'] = overrides.pop(f'2025-{month:02}')
        self.db.commit()
        item = self.client.post(self.config_url, json=self.config_body).json()['data']
        base = f"/schools/1/sessions/1/fee_configurations/{item['id']}"
        path = base + f"/fee_heads/{self.heads[0]}/months"
        scope = ''
        response = self.client.get(base + '/months' + scope)
        self.assertEqual(response.status_code, 200, response.text)
        months = response.json()['data']['fee_heads'][0]['months']
        self.assertEqual(months[0]['month'], '2025-04')
        self.assertEqual(months[-1]['month'], '2026-03')
        for body in ({}, {'amount': None}, {'amount': '-1'}, {'month_amounts': {'2026-04': None}},
                     {'month_amounts': {'2025-13': {'amount': '1', 'enabled': True}}}):
            self.assertEqual(self.client.patch(path + scope, json=body).status_code, 422)
        body = {'month_amounts': {'2025-04': {'amount': '1', 'enabled': False}}}
        self.assertEqual(self.client.patch(path.replace('/schools/1/sessions/1/', '/schools/1/sessions/3/'), json=body).status_code, 404)
        self.assertEqual(self.client.get(base.replace('/schools/1/sessions/1/', '/schools/2/sessions/2/') + '/months').status_code, 404)
        self.assertEqual(self.client.patch(base + '/fee_heads/999/months' + scope, json=body).status_code, 404)
        self.assertEqual(self.client.get(base + scope).json()['data'], item)

    def test_auto_creation(self):
        FeeConfigurationHead.__table__.drop(self.engine)
        FeeConfiguration.__table__.drop(self.engine)
        del self.client.app.dependency_overrides[get_db_session]
        with patch.object(database, 'engine', self.engine), patch.object(database, 'SessionLocal', lambda: database.sessionmaker(bind=self.engine)()):
            response = self.client.post(self.config_url, json=self.config_body)
        self.assertEqual(response.status_code, 201, response.text)

    def test_complete_month_json_and_required_edit_values(self):
        body = dict(self.config_body, no_of_fee_heads=1, fee_heads=[{'fee_head_id': self.heads[0], 'amount': '500', 'edit_months_wise': False, 'month_amounts': {}}])
        response = self.client.post(self.config_url, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        head = item['fee_heads'][0]
        self.assertFalse(head['edit_months_wise'])
        self.assertEqual(len(head['month_amounts']), 12)
        self.assertTrue(all(value == '500.00' for value in head['month_amounts'].values()))
        url = '/schools/fee_configurations?school_id=1&session_id=1&configuration_id=' + str(item['id'])
        body['fee_heads'][0].update(edit_months_wise=True, month_amounts={'2025-04': '450'})
        self.assertEqual(self.client.put(url, json=body).status_code, 422)
        body['fee_heads'][0]['month_amounts'] = dict(head['month_amounts'], **{'2025-04': '450'})
        response = self.client.put(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['fee_heads'][0]['month_amounts']['2025-04'], '450')

import unittest
from unittest.mock import patch

import test_sessions
from database import database
from dependencies.db import get_db_session
from models.school import School
from models.school_mapping import SchoolMapping
from models.user import User, UserRole, UserStatus
from routers.super_admin import router
from sqlalchemy.exc import IntegrityError


class SchoolMappingTests(unittest.TestCase):
    def setUp(self):
        test_sessions.SessionTests.setUp(self)
        self.client.app.include_router(router)
        self.url = '/super-admin/school-mappings'
        self.list_url = '/super-admin/school/1/users'
        self.payload = {'school_id': 1, 'user_id': 1, 'status': 'active'}

    tearDown = test_sessions.SessionTests.tearDown

    def create(self, **changes):
        response = self.client.post(self.url, json=dict(self.payload, **changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['data']['id']

    def test_crud_status_and_school_scope(self):
        mapping_id = self.create()
        item_url = f'{self.url}/{mapping_id}'
        self.create(school_id=2)
        items = self.client.get(self.list_url).json()['data']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['user_id'], 1)
        self.assertNotIn('password', items[0])
        for status in ('deactive', 'active'):
            response = self.client.patch(f'{item_url}/status', json={'status': status})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data']['status'], status)
            self.assertEqual(len(self.client.get(self.list_url, params={'status': status}).json()['data']), 1)
            self.assertEqual(self.db.get(User, 1).status, UserStatus.ACTIVE)
        self.assertEqual(len(self.client.get(self.list_url, params={'status': 'deactive'}).json()['data']), 1)
        self.db.add(User(id=2, first_name='Other', last_name='User', email='other@example.com', mobile='23456'))
        self.db.commit()
        response = self.client.put(item_url, json={'school_id': 2, 'user_id': 2, 'status': 'deactive'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(self.list_url).json()['data'], [])
        self.assertEqual(self.client.delete(item_url).status_code, 200)
        self.assertEqual(self.client.delete(item_url).status_code, 404)
        self.assertIsNone(self.db.get(SchoolMapping, mapping_id))

    def test_duplicates_and_missing_resources(self):
        mapping_id = self.create()
        self.assertEqual(self.client.post(self.url, json=self.payload).status_code, 409)
        other = self.create(school_id=2)
        self.assertEqual(self.client.put(f'{self.url}/{other}', json=self.payload).status_code, 409)
        for changes in ({'school_id': 999}, {'user_id': 999}):
            payload = dict(self.payload, **changes)
            self.assertEqual(self.client.post(self.url, json=payload).status_code, 404)
            self.assertEqual(self.client.put(f'{self.url}/{mapping_id}', json=payload).status_code, 404)
        self.assertEqual(self.client.get('/super-admin/school/999/users').status_code, 404)
        self.assertEqual(self.client.put(f'{self.url}/999', json=self.payload).status_code, 404)
        self.assertEqual(self.client.patch(f'{self.url}/999/status', json={'status': 'active'}).status_code, 404)

    def test_sub_admin_can_only_have_one_school(self):
        self.db.get(User, 1).role = UserRole.SUB_ADMIN
        self.db.commit()
        mapping_id = self.create()
        response = self.client.post(self.url, json=dict(self.payload, school_id=2))
        self.assertEqual(response.status_code, 409, response.text)
        response = self.client.put(f'{self.url}/{mapping_id}', json=dict(self.payload, school_id=2))
        self.assertEqual(response.status_code, 200, response.text)
        self.db.add(User(id=2, first_name='Other', last_name='Admin', email='other@example.com',
                         mobile='23456', role=UserRole.ADMIN))
        self.db.commit()
        other_id = self.create(user_id=2)
        response = self.client.put(f'{self.url}/{other_id}', json=self.payload)
        self.assertEqual(response.status_code, 409, response.text)

    def test_super_admin_required_for_every_endpoint(self):
        mapping_id = self.create()
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            for method, url, payload in (
                ('post', self.url, self.payload),
                ('put', f'{self.url}/{mapping_id}', self.payload),
                ('patch', f'{self.url}/{mapping_id}/status', {'status': 'deactive'}),
                ('delete', f'{self.url}/{mapping_id}', None),
                ('get', self.list_url, None),
            ):
                response = self.client.request(method, url, **({'json': payload} if payload else {}))
                self.assertEqual(response.status_code, 403, response.text)

    def test_validation(self):
        for changes in ({'status': 'pending'}, {'status': None}, {'school_id': 0}, {'user_id': -1}):
            self.assertEqual(self.client.post(self.url, json=dict(self.payload, **changes)).status_code, 422)
        self.assertEqual(self.client.post(self.url, json={'school_id': 1, 'user_id': 1}).status_code, 422)
        self.assertEqual(self.client.get(self.list_url, params={'status': 'bad'}).status_code, 200)

    def test_list_includes_active_and_inactive_users_and_assignments(self):
        self.create()
        self.db.add(User(id=2, first_name='Inactive', last_name='User',
                         email='inactive@example.com', mobile='23456', status=UserStatus.INACTIVE))
        self.db.commit()
        self.create(user_id=2, status='deactive')
        for params in ({}, {'status': 'active'}, {'status': 'deactive'}):
            response = self.client.get(self.list_url, params=params)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({item['user_id'] for item in response.json()['data']}, {1, 2})

    def test_constraints_and_parent_deletion(self):
        self.create()
        for values in (self.payload, dict(self.payload, school_id=999), dict(self.payload, user_id=999),
                       dict(self.payload, school_id=2, status='bad')):
            self.db.add(SchoolMapping(**values))
            with self.assertRaises(IntegrityError):
                self.db.commit()
            self.db.rollback()
        self.db.delete(self.db.get(School, 1))
        self.db.commit()
        self.assertEqual(self.db.query(SchoolMapping).count(), 0)
        self.create(school_id=2)
        self.db.delete(self.db.get(User, 1))
        self.db.commit()
        self.assertEqual(self.db.query(SchoolMapping).count(), 0)

    def test_automatic_table_creation(self):
        SchoolMapping.__table__.drop(self.engine)
        del self.client.app.dependency_overrides[get_db_session]
        with patch.object(database, 'engine', self.engine), patch.object(
            database, 'SessionLocal', lambda: database.sessionmaker(bind=self.engine)()
        ):
            self.create()

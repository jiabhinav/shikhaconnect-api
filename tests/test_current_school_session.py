import unittest
from datetime import date
from unittest.mock import patch

import test_sessions
from models.session import Session as SchoolSession
from models.school import School
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.user import User
from routers.super_admin import router as super_router
from routers.users import router as users_router
from routers.auth import router as auth_router
from utils.school_sessions import current_session_ids


class CurrentSchoolSessionTests(unittest.TestCase):
    def setUp(self):
        test_sessions.SessionTests.setUp(self)
        for router in (super_router, users_router, auth_router):
            self.client.app.include_router(router)
        self.db.add_all([
            SchoolSession(id=10, school_id=1, name='Earlier', start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
            SchoolSession(id=11, school_id=1, name='Current', start_date=date(2026, 4, 1), end_date=date(2027, 3, 31)),
            SchoolSession(id=12, school_id=1, name='Latest tie', start_date=date(2026, 4, 1), end_date=date(2027, 3, 31)),
            SchoolSession(id=20, school_id=2, name='Upcoming', start_date=date(2027, 1, 1), end_date=date(2027, 12, 31)),
        ])
        for school in self.db.query(School).all():
            school.primary_email = f"school{school.id}@example.com"
            school.primary_number = "1234567890"
        user = self.db.get(User, 1)
        user.password = 'secret'
        self.db.add_all([SchoolMapping(school_id=school_id, user_id=user.login_user_id,
                                       status=SchoolMappingStatus.ACTIVE) for school_id in (1, 2)])
        self.db.commit()

    tearDown = test_sessions.SessionTests.tearDown

    def test_current_selection_and_inclusive_boundaries(self):
        self.assertEqual(current_session_ids(self.db, [1, 2], as_of=date(2026, 9, 23)), {1: 12, 2: None})
        self.assertEqual(current_session_ids(self.db, [1], as_of=date(2026, 4, 1)), {1: 12})
        self.assertEqual(current_session_ids(self.db, [1], as_of=date(2027, 3, 31)), {1: 12})
        self.assertEqual(current_session_ids(self.db, [1], as_of=date(2027, 4, 1)), {1: None})

    def test_school_lists_details_and_assigned_schools_include_current_id(self):
        for prefix, clock in (('/schools', 'routers.schools.get_today'),
                              ('/super-admin', 'routers.super_admin.get_today')):
            with patch(clock, return_value=date(2026, 9, 23)):
                response = self.client.get(prefix + '/school')
                self.assertEqual(response.status_code, 200, response.text)
                schools = response.json()['data']
                self.assertEqual({s['id']: s['current_session_id'] for s in schools}, {1: 12, 2: None})
                for school in schools:
                    detail = self.client.get(f"{prefix}/school/{school['id']}").json()['data']
                    self.assertEqual(detail['current_session_id'], school['current_session_id'])
        with patch('utils.school_sessions.today', return_value=date(2026, 9, 23)):
            for url in ('/users/', '/users/1'):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()['data']
                user = data[0] if isinstance(data, list) else data
                self.assertEqual({s['id']: s['current_session_id'] for s in user['schools']}, {1: 12, 2: None})
            response = self.client.post('/auth/login', json={'mobile': '12345', 'password': 'secret'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({s['id']: s['current_session_id'] for s in response.json()['data']['schools']}, {1: 12, 2: None})

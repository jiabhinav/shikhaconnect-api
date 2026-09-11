import unittest
import test_sessions
from models.timetable_settings import TimetableSettings
from models.school import SchoolUserAssignment
from models.user import UserRole
from database.table_init import ensure_all_tables


class TimetableSettingsTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def prepare(self):
        sid = self.client.post('/schools/school/1/sessions', json=self.payload).json()['data']['id']
        return f'/schools/school/1/sessions/{sid}/timetable-settings', {
            'summer_start_time':'08:00:00', 'summer_end_time':'14:00:00',
            'winter_start_time':'09:00:00', 'winter_end_time':'15:00:00',
            'minimum_attendance_percentage':75.5, 'term_attendance_enabled':True}

    def test_create_update_get(self):
        url, payload = self.prepare()
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.put(url, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()['data']
        self.assertEqual(self.client.get(url).json()['data'], item)
        updated = self.client.put(url, json=dict(payload, term_attendance_enabled=False))
        self.assertEqual(updated.json()['data']['id'], item['id'])
        self.assertFalse(updated.json()['data']['term_attendance_enabled'])
        self.assertEqual(self.db.query(TimetableSettings).count(), 1)

    def test_validation_and_scope(self):
        url, payload = self.prepare()
        for change in ({'summer_end_time':'07:00'}, {'winter_end_time':'09:00'},
                       {'minimum_attendance_percentage':101}, {'minimum_attendance_percentage':-1},
                       {'minimum_attendance_percentage':75.555}, {'summer_start_time':'08:00Z'},
                       {'winter_start_time':1234}, {'term_attendance_enabled':'yes'}):
            self.assertEqual(self.client.put(url, json=dict(payload, **change)).status_code, 422)
        self.assertEqual(self.client.put(url.replace('/school/1/', '/school/2/'), json=payload).status_code, 404)
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.put(url, json=payload).status_code, 404)
            self.assertEqual(self.client.get(url).status_code, 404)
            assignment = SchoolUserAssignment(school_id=1, user_id=1, role=role.value)
            self.db.add(assignment)
            self.db.commit()
            self.assertEqual(self.client.put(url, json=payload).status_code, 200)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.db.delete(assignment)
            self.db.commit()

    def test_auto_creation(self):
        TimetableSettings.__table__.drop(self.engine)
        with self.engine.begin() as c:
            ensure_all_tables(c)
        self.assertEqual(self.db.query(TimetableSettings).count(), 0)

    def test_post_creates_and_does_not_overwrite(self):
        url, payload = self.prepare()
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        original = response.json()['data']
        self.assertEqual(self.client.post(url, json=dict(payload, term_attendance_enabled=False)).status_code, 409)
        self.assertEqual(self.client.get(url).json()['data'], original)
        self.assertEqual(self.db.query(TimetableSettings).count(), 1)
        self.assertEqual(self.client.post(url.replace('/school/1/', '/school/2/'), json=payload).status_code, 404)
        self.assertEqual(self.client.post(url, json=dict(payload, minimum_attendance_percentage=101)).status_code, 422)
        self.user.role = UserRole.ADMIN
        self.assertEqual(self.client.post(url, json=payload).status_code, 404)

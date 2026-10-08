from copy import deepcopy
import unittest

import test_sessions
from dependencies.auth import get_current_user, account_token
from models.caste_category import CasteCategory
from models.teacher import Teacher, TeacherAddress, TeacherLogin
from models.user import LoginUser
from routers.auth import router as auth_router
from routers.teachers import router as teacher_router
from utils.passwords import verify_password
from utils.school_deletion import delete_school_data
from models.school import School


class TeacherTests(unittest.TestCase):
    def setUp(self):
        test_sessions.SessionTests.setUp(self)
        self.user.login_user_id = self.db.query(LoginUser.id).scalar()
        self.client.app.include_router(auth_router)
        self.client.app.include_router(teacher_router)
        school = self.db.get(School, 1)
        school.primary_email = 'school@example.com'
        school.primary_number = '1234567890'
        self.db.add_all([CasteCategory(id=1, school_id=1, name='General'),
                         CasteCategory(id=2, school_id=2, name='General')])
        self.db.commit()
        self.url = '/schools/teachers?school_id=1'
        self.payload = {
            'teacher_info': {
                'first_name': 'Teacher', 'father_name': 'Father', 'mother_name': 'Mother',
                'date_of_birth': '1990-01-01', 'gender': 'Female',
                'mobile_number': '9876543210', 'alternate_contact_no': '9876543211',
                'email': 'teacher@example.com', 'aadhaar_number': '123456789012',
                'designation': 'Teacher', 'nationality': 'Indian', 'caste_category_id': 1,
                'salary': '25000.50',
            },
            'address': {'line_1': 'Street', 'city': 'Delhi', 'country': 'India',
                                'state': 'Delhi', 'district': 'Central', 'landline_number': '011123456'},
        }

    tearDown = test_sessions.SessionTests.tearDown

    def create(self):
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['data']

    def detail_url(self, item):
        return f"/schools/teachers/{item['id']}?school_id=1"

    def test_crud_addresses_and_password(self):
        item = self.create()
        teacher = self.db.get(Teacher, item['id'])
        password = teacher.password
        self.assertNotEqual(password, self.payload['teacher_info']['mobile_number'])
        self.assertTrue(verify_password('9876543210', password))
        self.assertNotIn('password', item['teacher_info'])
        self.assertEqual(item['address']['district'], 'Central')
        self.assertIsNone(item['address']['pin_code'])
        self.assertEqual(self.client.get(self.detail_url(item)).json()['data'], item)
        self.assertEqual(self.client.get(self.url).json()['data'], [item])
        self.assertEqual(self.client.get(self.url + '&offset=1').json()['data'], [])
        updated = deepcopy(self.payload)
        updated['teacher_info']['first_name'] = 'Updated'
        updated['teacher_info']['mobile_number'] = '9876543212'
        updated['address']['district'] = 'South'
        response = self.client.put(self.detail_url(item), json=updated)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['address']['district'], 'South')
        self.assertEqual(self.db.get(Teacher, item['id']).password, password)
        self.assertEqual(self.db.query(TeacherAddress).count(), 1)
        self.assertEqual(self.client.delete(self.detail_url(item)).status_code, 200)
        self.assertIsNone(self.db.get(TeacherLogin, item['teacher_login_id']))
        self.assertEqual(self.db.query(TeacherAddress).count(), 0)
        self.assertEqual(self.client.get(self.detail_url(item)).status_code, 404)

    def test_scope_duplicates_and_validation(self):
        item = self.create()
        self.assertEqual(self.client.post(self.url, json=self.payload).status_code, 409)
        self.assertEqual(self.client.get('/schools/teachers?school_id=2').json()['data'], [])
        wrong_url = f"/schools/teachers/{item['id']}?school_id=2"
        for method in ('get', 'delete'):
            self.assertEqual(getattr(self.client, method)(wrong_url).status_code, 404)
        self.assertEqual(self.client.put(wrong_url, json=self.payload).status_code, 404)
        self.assertEqual(self.client.patch(wrong_url.replace('?','/status?'),
                                          json={'status': 'Inactive'}).status_code, 404)
        payload = deepcopy(self.payload)
        payload['teacher_info']['caste_category_id'] = 2
        self.assertEqual(self.client.put(self.detail_url(item), json=payload).status_code, 404)
        for field in ('mother_name', 'first_name'):
            payload = deepcopy(self.payload)
            payload['teacher_info'][field] = ''
            self.assertEqual(self.client.post(self.url, json=payload).status_code, 422)
        self.assertEqual(self.client.post('/schools/teachers', json=self.payload).status_code, 422)

    def test_address_form_required_and_optional_fields(self):
        for field in ('line_1', 'country', 'state'):
            payload = deepcopy(self.payload)
            del payload['address'][field]
            self.assertEqual(self.client.post(self.url, json=payload).status_code, 422)
        self.payload['address'] = {'line_1': 'Street', 'country': 'India', 'state': 'Delhi'}
        item = self.create()
        for field in ('line_2', 'city', 'district', 'landline_number', 'pin_code'):
            self.assertIsNone(item['address'][field])
        self.assertNotIn('present_address', item)
        self.assertNotIn('permanent_address', item)
        self.payload['address']['city'] = ''
        response = self.client.put(self.detail_url(item), json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['data']['address']['city'])

    def test_optional_email_and_aadhaar(self):
        for index, mode in enumerate(('omitted', None, '')):
            payload = deepcopy(self.payload)
            payload['teacher_info']['mobile_number'] = f'987654321{index}'
            for field in ('email', 'aadhaar_number'):
                if mode == 'omitted':
                    payload['teacher_info'].pop(field)
                else:
                    payload['teacher_info'][field] = mode
            response = self.client.post(self.url, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            item = response.json()['data']
            self.assertIsNone(item['teacher_info']['email'])
            self.assertIsNone(item['teacher_info']['aadhaar_number'])
            response = self.client.put(self.detail_url(item), json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            mobile = payload['teacher_info']['mobile_number']
            response = self.client.post('/auth/login', json={'mobile': mobile, 'password': mobile})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()['data']['email'])

    def test_teacher_login_is_independent_of_shared_accounts(self):
        admin = self.db.query(LoginUser).one()
        original_password = admin.password
        self.payload['teacher_info']['mobile_number'] = admin.mobile
        self.payload['teacher_info']['email'] = admin.email
        item = self.create()
        self.assertEqual(self.db.query(LoginUser).count(), 1)
        self.assertEqual(self.db.query(TeacherLogin).count(), 1)
        self.assertNotIn('login_user_id', item)
        credentials = {'mobile': admin.mobile, 'password': admin.mobile}
        response = self.client.post('/auth/teacher-login', json=credentials)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['role'], 'Teacher')
        self.assertEqual(self.client.post('/auth/teacher-reset-password',
                         json={'mobile': admin.mobile, 'new_password': 'new-teacher-password'}).status_code, 200)
        self.assertEqual(self.client.post('/auth/teacher-login', json=credentials).status_code, 401)
        self.assertEqual(self.client.post('/auth/teacher-login',
                         json=dict(credentials, password='new-teacher-password')).status_code, 200)
        self.assertEqual(self.db.get(LoginUser, admin.id).password, original_password)
        self.assertEqual(self.client.delete(self.detail_url(item)).status_code, 200)
        self.assertIsNotNone(self.db.get(LoginUser, admin.id))
        self.assertEqual(self.db.query(TeacherLogin).count(), 0)

    def test_employee_codes_follow_school_settings_and_preserve_on_edit(self):
        school = self.db.get(School, 1)
        school.employee_prefix = 'EMP-'
        school.employee_suffix = '-T'
        school.start_employee_no = 100
        self.db.commit()
        first = self.create()
        self.assertEqual(first['teacher_info']['employee_code'], 'EMP-100-T')
        self.payload['teacher_info']['mobile_number'] = '9876543220'
        self.payload['teacher_info']['email'] = 'second@example.com'
        second = self.create()
        self.assertEqual(second['teacher_info']['employee_code'], 'EMP-101-T')
        school.employee_prefix = 'NEW-'
        school.start_employee_no = 200
        self.db.commit()
        response = self.client.put(self.detail_url(second), json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['teacher_info']['employee_code'], 'EMP-101-T')
        self.payload['teacher_info']['mobile_number'] = '9876543230'
        self.payload['teacher_info']['email'] = None
        third = self.create()
        self.assertEqual(third['teacher_info']['employee_code'], 'NEW-200-T')
        other_school = self.db.get(School, 2)
        other_school.employee_prefix = None
        other_school.employee_suffix = None
        other_school.start_employee_no = 0
        self.db.commit()
        payload = deepcopy(self.payload)
        payload['teacher_info']['mobile_number'] = '9876543240'
        payload['teacher_info']['caste_category_id'] = 2
        response = self.client.post('/schools/teachers?school_id=2', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['data']['teacher_info']['employee_code'], '0')

    def test_existing_teacher_gets_employee_code_when_edited(self):
        item = self.create()
        teacher = self.db.get(Teacher, item['id'])
        teacher.employee_code = None
        teacher.employee_sequence = None
        self.db.commit()
        response = self.client.put(self.detail_url(item), json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(response.json()['data']['teacher_info']['employee_code'])

    def test_status_login_and_tokens(self):
        item = self.create()
        credentials = {'mobile': '9876543210', 'password': '9876543210'}
        response = self.client.post('/auth/login', json=credentials)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['schools'][0]['id'], 1)
        token = response.json()['token']
        self.assertEqual(token, account_token(self.db.get(Teacher, item['id'])))
        self.client.app.dependency_overrides.pop(get_current_user)
        response = self.client.get(self.detail_url(item), headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(response.status_code, 200, response.text)
        self.client.app.dependency_overrides[get_current_user] = lambda: self.user
        url = f"/schools/teachers/{item['id']}/status?school_id=1"
        self.assertEqual(self.client.patch(url, json={'status': 'Inactive'}).status_code, 200)
        self.assertEqual(self.client.get(self.url + '&status=Active').json()['data'], [])
        self.assertEqual(len(self.client.get(self.url + '&status=Inactive').json()['data']), 1)
        self.assertEqual(self.client.post('/auth/login', json=credentials).status_code, 403)
        self.client.app.dependency_overrides.pop(get_current_user)
        self.assertEqual(self.client.get(self.detail_url(item),
                                        headers={'Authorization': f'Bearer {token}'}).status_code, 403)
        self.client.app.dependency_overrides[get_current_user] = lambda: self.user
        self.assertEqual(self.client.patch(url, json={'status': 'Active'}).status_code, 200)
        self.assertEqual(self.client.post('/auth/login', json=credentials).status_code, 200)
        self.assertEqual(self.client.patch(url, json={'status': 'Pending'}).status_code, 422)
        self.assertEqual(self.client.post('/auth/reset-password',
                        json={'mobile': '9876543210', 'new_password': 'changed'}).status_code, 200)
        self.assertEqual(self.client.post('/auth/login', json=credentials).status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json=dict(credentials, password='changed')).status_code, 200)

    def test_school_deletion_removes_teacher_account_and_addresses(self):
        item = self.create()
        delete_school_data(self.db, self.db.get(School, 1))
        self.db.commit()
        self.assertEqual(self.db.query(Teacher).count(), 0)
        self.assertEqual(self.db.query(TeacherAddress).count(), 0)
        self.assertIsNone(self.db.get(TeacherLogin, item['teacher_login_id']))
        self.assertIsNotNone(self.db.get(School, 2))

import unittest
import test_sessions
from schemas.student import StudentInfo, ParentInfo, StudentAddress
from models.student import Student
from models.user import UserRole
from database.table_init import ensure_all_tables


class StudentTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def student_payload(self):
        base='/schools/school/1'
        data={k:'Test' for k,v in {**StudentInfo.model_fields, **ParentInfo.model_fields, **StudentAddress.model_fields}.items() if v.is_required()}
        data.update(email='student@example.com',date_of_birth='2015-01-01',mobile_number='123456', father_contact_no='123456',father_aadhaar_no='123456789012',pin_code='123456')
        data['session_id']=self.client.post(base+'/sessions',json=self.payload).json()['data']['id']
        for field, route in [('caste_category_id','caste_categories'),('fee_category_id','fee_categories'),('class_id','classes'),('section_id','sections')]:
            data[field]=self.client.post(f"{base}/sessions/{data['session_id']}/{route}",json={'name':'Test'}).json()['data']['id']
        return data

    def test_admission_date_defaults_and_updates(self):
        from datetime import date
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        for value in ('omitted', None, ''):
            if value != 'omitted':
                payload['student_info']['admission_date'] = value
            response = self.client.post(url, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()['data']['student_info']['admission_date'], date.today().isoformat())
        payload['student_info']['admission_date'] = '2024-07-15'
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertEqual(item['student_info']['admission_date'], '2024-07-15')
        del payload['student_info']['admission_date']
        response = self.client.put(f"{url}/{item['id']}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['student_info']['admission_date'], '2024-07-15')
        payload['student_info']['admission_date'] = '2024-08-01'
        response = self.client.put(f"{url}/{item['id']}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['student_info']['admission_date'], '2024-08-01')
        fetched = self.client.get(f"{url}/{item['id']}")
        self.assertEqual(fetched.json()['data']['student_info']['admission_date'], '2024-08-01')
        payload['student_info']['admission_date'] = 'invalid-date'
        self.assertEqual(self.client.post(url, json=payload).status_code, 422)

    def test_previous_school_and_extra_form_fields(self):
        from models.student import PreviousSchool
        payload = nest(self.student_payload())
        payload['student_info'].update(student_type='Day scholar', admission_type='New',
            first_admission_class='Nursery', abha_number='123', mode_of_transport='Bus',
            weight_kg=25.5, height_cm=120)
        payload['present_address']['landline_number'] = '0123456789'
        payload['previous_school'] = {'school_name': 'Old School', 'address': 'Old address',
                                     'class_name': 'Nursery', 'session': '2025-2026'}
        url = '/schools/1/students'
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()['data']
        self.assertEqual(data['previous_school']['student_id'], data['id'])
        self.assertEqual(data['previous_school']['school_id'], 1)
        self.assertEqual(data['student_info']['weight_kg'], 25.5)
        self.assertEqual(data['present_address']['landline_number'], '0123456789')
        payload['previous_school']['school_name'] = 'Updated School'
        response = self.client.put(f"{url}/{data['id']}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['previous_school']['school_name'], 'Updated School')
        self.assertEqual(self.db.query(PreviousSchool).count(), 1)
        self.assertEqual(self.client.delete('/schools/students', params={'school_id': 1, 'student_id': data['id']}).status_code, 204)
        self.assertEqual(self.db.query(PreviousSchool).count(), 0)

    def test_form_field_migration_preserves_rows(self):
        from sqlalchemy import create_engine, text
        from database.student_table import migrate_student_form_fields
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE students (id INTEGER PRIMARY KEY)'))
            connection.execute(text('INSERT INTO students VALUES (1)'))
            connection.execute(text('CREATE TABLE student_addresses (id INTEGER PRIMARY KEY)'))
            migrate_student_form_fields(connection)
            migrate_student_form_fields(connection)
            self.assertEqual(connection.execute(text('SELECT id, weight_kg, house_id, father_dob, mother_dob, admission_date FROM students')).all(), [(1, None, None, None, None, None)])
        engine.dispose()

    def test_optional_student_dob(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        del payload['student_info']['date_of_birth']
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        student_id = response.json()['data']['id']
        self.assertIsNone(response.json()['data']['student_info']['date_of_birth'])
        for value in (None, '', '   ', '2015-08-31'):
            payload['student_info']['date_of_birth'] = value
            response = self.client.put(f'{url}/{student_id}', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            expected = value if value == '2015-08-31' else None
            self.assertEqual(response.json()['data']['student_info']['date_of_birth'], expected)
            fetched = self.client.get(f'{url}/{student_id}').json()['data']
            self.assertEqual(fetched['student_info']['date_of_birth'], expected)
        payload['student_info']['date_of_birth'] = 'invalid-date'
        self.assertEqual(self.client.put(f'{url}/{student_id}', json=payload).status_code, 422)

    def test_parent_full_dob_field_names(self):
        from schemas.student import StudentWrite
        payload = nest(self.student_payload())
        payload['parent_info'].update(father_date_of_birth='', mother_date_of_birth='')
        parsed = StudentWrite.model_validate(payload)
        self.assertIsNone(parsed.parent_info.father_dob)
        self.assertIsNone(parsed.parent_info.mother_dob)
        payload['parent_info'].update(father_date_of_birth='1985-03-15', mother_date_of_birth='1988-07-20')
        response = self.client.post('/schools/1/students', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()['data']
        self.assertEqual(data['parent_info']['father_date_of_birth'], '1985-03-15')
        self.assertEqual(data['parent_info']['mother_date_of_birth'], '1988-07-20')
        student = self.db.get(Student, data['id'])
        self.assertEqual(student.father_dob.isoformat(), '1985-03-15')
        self.assertEqual(student.mother_dob.isoformat(), '1988-07-20')

    def test_parent_dates_of_birth(self):
        payload = nest(self.student_payload())
        payload['parent_info'].update(father_dob='1985-03-15', mother_dob='1988-07-20')
        url = '/schools/1/students'
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()['data']
        student_id = data['id']
        self.assertEqual(data['parent_info']['father_date_of_birth'], '1985-03-15')
        self.assertEqual(data['parent_info']['mother_date_of_birth'], '1988-07-20')
        self.assertEqual(self.client.get(f'{url}/{student_id}').json()['data']['parent_info'], data['parent_info'])
        payload['parent_info'].update(father_dob='1986-03-15', mother_dob='')
        response = self.client.put(f'{url}/{student_id}', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['parent_info']['father_date_of_birth'], '1986-03-15')
        self.assertIsNone(response.json()['data']['parent_info']['mother_date_of_birth'])
        payload['parent_info']['father_dob'] = 'invalid-date'
        self.assertEqual(self.client.put(f'{url}/{student_id}', json=payload).status_code, 422)

    def test_required_only_create_edit_update(self):
        payload=self.student_payload()
        url='/schools/1/students'
        response=self.client.post(url,json=nest(payload))
        self.assertEqual(response.status_code,201,response.text)
        item=response.json()['data']
        self.assertIsNone(item['parent_info']['guardian_email'])
        self.assertNotIn('address', item)
        self.assertNotIn('address_type', item['present_address'])
        self.assertNotIn('address_type', item['permanent_address'])
        item_url=f"{url}/{item['id']}"
        self.assertEqual(self.client.get(item_url).json()['data'],item)
        response=self.client.put(item_url,json=nest(dict(payload,first_name='Edited',guardian_email='',mother_contact_no='')))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['data']['student_info']['first_name'],'Edited')
        self.assertIsNone(response.json()['data']['parent_info']['mother_contact_no'])
        self.assertEqual(len(self.client.get(url).json()['data']),1)
        self.assertEqual(self.client.get(url+'?offset=1').json()['data'],[])

    def test_required_and_reference_validation(self):
        payload=self.student_payload()
        url='/schools/1/students'
        for key,field in {**StudentInfo.model_fields, **ParentInfo.model_fields, **StudentAddress.model_fields}.items():
            if field.is_required():
                missing=dict(payload)
                del missing[key]
                self.assertEqual(self.client.post(url,json=nest(missing)).status_code,422,key)
        for field in ('class_id','section_id','session_id','fee_category_id','caste_category_id'):
            self.assertEqual(self.client.post(url,json=nest(dict(payload,**{field:99999}))).status_code,404)
        other_session=self.client.post('/schools/school/2/sessions',json=self.payload).json()['data']['id']
        foreign=self.client.post(f'/schools/school/2/sessions/{other_session}/classes',json={'name':'Other'}).json()['data']['id']
        self.assertEqual(self.client.post(url,json=nest(dict(payload,class_id=foreign))).status_code,404)
        self.assertEqual(self.client.post(url,json=nest(dict(payload,email='bad'))).status_code,422)

    def test_login_sequence_and_two_addresses(self):
        from models.school import School
        from models.student import StudentLogin, StudentAddressRecord
        from utils.passwords import verify_password
        school = self.db.get(School, 1)
        school.admission_prefix = "ADM-"
        school.admission_suffix = "-S"
        school.start_admission_no = 100
        self.db.commit()
        payload = nest(self.student_payload())
        payload['student_info']['apar_id'] = 'APAR-1'
        address = payload["present_address"]
        payload.update(present_address=dict(address, district="Present district"),
                       permanent_address=dict(address, city="Permanent city"))
        url = '/schools/1/students'
        first = self.client.post(url, json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        data = first.json()['data']
        self.assertEqual(data['student_info']['admission_number'], 'ADM-100-S')
        self.assertNotIn('password', data['login'])
        self.assertEqual(data['student_info']['section_id'], payload['student_info']['section_id'])
        self.assertEqual(data['student_info']['apar_id'], payload['student_info']['apar_id'])
        self.assertEqual(data['present_address']['district'], 'Present district')
        self.assertEqual(data['permanent_address']['city'], 'Permanent city')
        account = self.db.get(Student, data['id']).login
        self.assertTrue(verify_password(payload['student_info']['mobile_number'], account.password))
        self.assertEqual(self.db.query(StudentAddressRecord).count(), 2)
        second = self.client.post(url, json=payload)
        self.assertEqual(second.json()['data']['student_info']['admission_number'], 'ADM-101-S')
        payload['student_info']['mobile_number'] = '9999999999'
        edited = self.client.put(f"{url}/{data['id']}", json=payload)
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()['data']['student_info']['admission_number'], 'ADM-100-S')
        self.assertEqual(edited.json()['data']['login']['mobile'], '9999999999')
        self.assertEqual(self.db.query(StudentAddressRecord).count(), 4)
        self.assertTrue(verify_password(payload['student_info']['mobile_number'], self.db.get(Student, data['id']).login.password))
        del payload['permanent_address']
        self.assertEqual(self.client.post(url, json=payload).status_code, 422)

    def test_failed_save_rolls_back_login_and_addresses(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        from models.student import StudentLogin, StudentAddressRecord
        payload = nest(self.student_payload())
        with patch.object(self.db, 'commit', side_effect=IntegrityError('insert', {}, Exception('failure'))):
            response = self.client.post('/schools/1/students', json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.query(Student).count(), 0)
        self.assertEqual(self.db.query(StudentLogin).count(), 0)
        self.assertEqual(self.db.query(StudentAddressRecord).count(), 0)
        response = self.client.post('/schools/1/students', json=payload)
        self.assertEqual(response.json()['data']['student_info']['admission_number'], '1')

    def test_section_scope(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        other_session = self.client.post('/schools/school/2/sessions', json=self.payload).json()['data']['id']
        section = self.client.post(f'/schools/school/2/sessions/{other_session}/sections', json={'name': 'Other'}).json()['data']['id']
        payload['student_info']['section_id'] = section
        self.assertEqual(self.client.post(url, json=payload).status_code, 404)

    def test_optional_last_name_and_apar_id(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        for fields in ({}, {'last_name': None, 'apar_id': None},
                       {'last_name': '', 'apar_id': '   '}):
            info = {k: v for k, v in payload['student_info'].items()
                    if k not in ('last_name', 'apar_id')} | fields
            response = self.client.post(url, json=payload | {'student_info': info})
            self.assertEqual(response.status_code, 201, response.text)
            student_id = response.json()['data']['id']
            for field in ('last_name', 'apar_id'):
                self.assertIsNone(response.json()['data']['student_info'][field])
                self.assertIsNone(getattr(self.db.get(Student, student_id), field))
            fetched = self.client.get(f'{url}/{student_id}')
            self.assertEqual(fetched.status_code, 200, fetched.text)

    def test_optional_emails(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        for fields in ({}, {'email': None, 'guardian_email': None},
                       {'email': '', 'guardian_email': '   '}):
            info = {k: v for k, v in payload['student_info'].items() if k != 'email'}
            parent = {k: v for k, v in payload['parent_info'].items() if k != 'guardian_email'}
            if 'email' in fields:
                info['email'] = fields['email']
                parent['guardian_email'] = fields['guardian_email']
            body = payload | {'student_info': info, 'parent_info': parent}
            response = self.client.post(url, json=body)
            self.assertEqual(response.status_code, 201, response.text)
            student_id = response.json()['data']['id']
            self.assertIsNone(response.json()['data']['student_info']['email'])
            self.assertIsNone(response.json()['data']['parent_info']['guardian_email'])
            self.assertIsNone(self.db.get(Student, student_id).email)
            fetched = self.client.get(f'{url}/{student_id}')
            self.assertEqual(fetched.status_code, 200, fetched.text)
            updated = self.client.put(f'{url}/{student_id}', json=body)
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertIsNone(updated.json()['data']['student_info']['email'])
        for section, field in (('student_info', 'email'), ('parent_info', 'guardian_email')):
            invalid = payload | {section: payload[section] | {field: 'invalid-email'}}
            self.assertEqual(self.client.post(url, json=invalid).status_code, 422)

    def test_section_migration_is_repeatable(self):
        from sqlalchemy import create_engine, inspect, text
        from database.student_table import migrate_student_section
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE sections (id INTEGER PRIMARY KEY)'))
                connection.execute(text('CREATE TABLE students (id INTEGER PRIMARY KEY)'))
                connection.execute(text('INSERT INTO students (id) VALUES (1)'))
                migrate_student_section(connection)
                migrate_student_section(connection)
                self.assertIn('section_id', {c['name'] for c in inspect(connection).get_columns('students')})
                self.assertEqual(connection.execute(text('SELECT id, section_id FROM students')).all(), [(1, None)])
        finally:
            engine.dispose()

    def test_optional_father_fields_and_required_mother_name(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        for field in ('father_contact_no', 'father_aadhaar_no'):
            payload['parent_info'].pop(field)
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        for field in ('father_contact_no', 'father_aadhaar_no'):
            self.assertIsNone(item['parent_info'][field])
        for value in (None, '', '   '):
            payload['parent_info'].update(father_contact_no=value, father_aadhaar_no=value)
            response = self.client.put(f"{url}/{item['id']}", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()['data']['parent_info']['father_contact_no'])
            self.assertIsNone(response.json()['data']['parent_info']['father_aadhaar_no'])
        for value in (None, '', '   '):
            payload['parent_info']['mother_name'] = value
            self.assertEqual(self.client.post(url, json=payload).status_code, 422)
        del payload['parent_info']['mother_name']
        self.assertEqual(self.client.post(url, json=payload).status_code, 422)

    def test_optional_address_pin_codes(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        for kind in ('present_address', 'permanent_address'):
            payload[kind].pop('pin_code')
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        for kind in ('present_address', 'permanent_address'):
            self.assertIsNone(item[kind]['pin_code'])
        for value in (None, '', '   ', '001234'):
            for kind in ('present_address', 'permanent_address'):
                payload[kind]['pin_code'] = value
            response = self.client.put(f"{url}/{item['id']}", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            for kind in ('present_address', 'permanent_address'):
                self.assertEqual(response.json()['data'][kind]['pin_code'], '001234' if value == '001234' else None)

    def test_admission_edit_requires_super_admin(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        item = self.client.post(url, json=payload).json()['data']
        edit_url = f"{url}/{item['id']}/admission-number"
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.patch(edit_url, json={'admission_number': '2'}).status_code, 403)
        self.user.role = UserRole.SUPER_ADMIN
        self.assertEqual(self.client.get(f"{url}/{item['id']}").json()['data']['student_info']['admission_number'], '1')
        edited = self.client.patch(edit_url, json={'admission_number': '2'})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()['data']['student_info']['admission_number'], '2')
        next_item = self.client.post(url, json=payload).json()['data']
        self.assertEqual(next_item['student_info']['admission_number'], '3')
        self.assertEqual(self.client.patch(edit_url, json={'admission_number': '3'}).status_code, 409)
        self.assertEqual(self.client.patch(edit_url, json={'admission_number': '   '}).status_code, 422)
        normal_update = self.client.put(f"{url}/{item['id']}", json=payload)
        self.assertEqual(normal_update.json()['data']['student_info']['admission_number'], '2')

    def test_optional_roll_number(self):
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        self.assertIsNone(item['student_info']['roll_number'])
        for value in ('001A', None, '', '   '):
            payload['student_info']['roll_number'] = value
            response = self.client.put(f"{url}/{item['id']}", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            expected = '001A' if value == '001A' else None
            self.assertEqual(response.json()['data']['student_info']['roll_number'], expected)
            self.assertEqual(self.client.get(f"{url}/{item['id']}").json()['data']['student_info']['roll_number'], expected)

    def test_roll_number_migration_is_repeatable(self):
        from sqlalchemy import create_engine, text
        from database.student_table import migrate_student_roll_number
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE students (id INTEGER PRIMARY KEY)'))
                connection.execute(text('INSERT INTO students (id) VALUES (1)'))
                migrate_student_roll_number(connection)
                migrate_student_roll_number(connection)
                self.assertEqual(connection.execute(text('SELECT id, roll_number FROM students')).all(), [(1, None)])
        finally:
            engine.dispose()

    def test_student_owns_admission_and_status(self):
        from models.student import StudentLogin
        payload = nest(self.student_payload())
        response = self.client.post('/schools/1/students', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()['data']
        student = self.db.get(Student, data['id'])
        self.assertEqual(student.status, 'active')
        self.assertEqual(student.admission_sequence, 1)
        self.assertEqual(student.login_id, student.login.id)
        self.assertEqual(data['student_info']['status'], 'active')
        self.assertEqual(data['student_info']['admission_sequence'], 1)
        for field in ('student_id', 'admission_number', 'admission_sequence', 'status'):
            self.assertNotIn(field, StudentLogin.__table__.columns)
            self.assertNotIn(field, data['login'])
        self.db.delete(student)
        self.db.commit()
        self.assertEqual(self.db.query(StudentLogin).count(), 1)

    def test_password_and_status_are_backend_managed(self):
        from schemas.student import StudentWrite
        payload = nest(self.student_payload())
        schema = StudentWrite.model_json_schema()
        self.assertNotIn('login', schema['properties'])
        self.assertNotIn('status', schema['$defs']['StudentInfo']['properties'])
        url = '/schools/1/students'
        self.assertEqual(self.client.post(url, json={**payload, 'login': {'password': 'custom-password'}}).status_code, 422)
        payload['student_info']['status'] = 'inactive'
        self.assertEqual(self.client.post(url, json=payload).status_code, 422)
        del payload['student_info']['status']
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        student = self.db.get(Student, response.json()['data']['id'])
        student.status = 'inactive'
        self.db.commit()
        password_hash = student.login.password
        response = self.client.put(f"{url}/{student.id}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['student_info']['status'], 'inactive')
        session_id = payload['student_info']['session_id']
        filtered = {'school_id': 1, 'session_id': session_id, 'status': False}
        inactive = self.client.get(url, params=filtered)
        self.assertEqual(inactive.status_code, 200, inactive.text)
        self.assertEqual([row['id'] for row in inactive.json()['data']], [first['id']])
        other_session = self.client.post('/schools/school/1/sessions', json={
            'name': 'Other', 'start_date': '2030-04-01', 'end_date': '2031-03-31'}).json()['data']['id']
        self.assertEqual(self.client.get(url, params=filtered | {'session_id': other_session}).json()['data'], [])
        self.assertEqual(self.client.patch(url + '/status', params=params | {'session_id': other_session},
                                          json={'status': True}).status_code, 404)
        self.assertEqual(self.client.get(url, params=filtered | {'session_id': 99999}).status_code, 404)
        self.assertEqual(self.client.get(url, params=filtered | {'session_id': 0}).status_code, 422)
        self.assertEqual(self.client.get(url, params=filtered | {'status': 'invalid'}).status_code, 422)
        self.assertEqual(self.client.patch(url + '/status', params={'school_id': 1, 'student_id': first['id']},
                                          json={'status': False}).status_code, 422)
        self.assertEqual(student.login.password, password_hash)

    def test_siblings_share_login_and_keep_separate_apar(self):
        from models.student import StudentLogin
        payload = nest(self.student_payload())
        url = '/schools/1/students'
        first = self.client.post(url, json=payload).json()['data']
        payload['student_info']['apar_id'] = 'SECOND-CHILD'
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        second = response.json()['data']
        self.assertEqual(first['login']['id'], second['login']['id'])
        self.assertEqual(self.db.query(StudentLogin).count(), 1)
        self.assertNotEqual(first['student_info']['admission_number'], second['student_info']['admission_number'])
        self.assertEqual(self.client.get(f"{url}/{first['id']}").json()['data']['student_info']['apar_id'], first['student_info']['apar_id'])
        payload['student_info']['mobile_number'] = '8888888888'
        response = self.client.put(f"{url}/{second['id']}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(first['login']['id'], response.json()['data']['login']['id'])
        self.assertEqual(self.client.get(f"{url}/{first['id']}").json()['data']['login']['mobile'], first['login']['mobile'])

    def test_dropdown_school_and_session_filters(self):
        payload = self.student_payload()
        from models.class_section import Section
        from models.house import House
        self.db.add_all([
            House(school_id=1, session_id=payload['session_id'], name='Blue'),
            House(school_id=1, session_id=None, name='Legacy house'),
            House(school_id=2, session_id=None, name='Other house'),
        ])
        self.db.add(Section(school_id=2, session_id=None, name='Other school'))
        self.db.add(Section(school_id=1, session_id=None, name='Legacy section'))
        self.db.commit()
        url = '/schools/1/students/dropdowns'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()['data']
        for key in ('caste_categories', 'fee_categories', 'classes', 'sections'):
            self.assertTrue(data[key])
        self.assertEqual({row['name'] for row in data['sections']}, {'Test', 'Legacy section'})
        self.assertEqual({row['name'] for row in data['houses']}, {'Blue', 'Legacy house'})
        response = self.client.get(url, params={'session_id': payload['session_id']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row['name'] for row in response.json()['data']['houses']], ['Blue'])
        self.assertEqual(response.json()['data']['houses'][0]['session_id'], payload['session_id'])
        self.assertEqual(response.json()['data']['sections'], [
            {'id': payload['section_id'], 'name': 'Test', 'session_id': payload['session_id']}])
        self.assertEqual(self.client.get(url, params={'session_id': 99999}).status_code, 404)
        self.assertEqual(self.client.get(url, params={'session_id': 0}).status_code, 422)
        self.assertEqual(self.client.get('/schools/99999/students/dropdowns').status_code, 404)
        other = self.client.post('/schools/school/2/sessions', json=self.payload).json()['data']['id']
        self.assertEqual(self.client.get(url, params={'session_id': other}).status_code, 404)

    def test_query_parameter_mutations(self):
        from models.student import StudentLogin, StudentAddressRecord
        payload = nest(self.student_payload())
        url = '/schools/students'
        self.assertEqual(self.client.post(url, json=payload).status_code, 422)
        response = self.client.post(url, params={'school_id': 1}, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        student_id = response.json()['data']['id']
        params = {'school_id': 1, 'student_id': student_id}
        payload['student_info']['first_name'] = 'Updated'
        response = self.client.put(url, params=params, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['student_info']['first_name'], 'Updated')
        response = self.client.patch(url + '/admission-number', params=params, json={'admission_number': 'MANUAL-10'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['student_info']['admission_number'], 'MANUAL-10')
        sibling = self.client.post(url, params={'school_id': 1}, json=payload).json()['data']
        self.assertEqual(self.client.delete(url, params={'school_id': 2, 'student_id': student_id}).status_code, 404)
        self.assertEqual(self.client.delete(url, params={'school_id': 1}).status_code, 422)
        self.assertEqual(self.client.delete(url, params=params).status_code, 204)
        self.assertIsNone(self.db.get(Student, student_id))
        self.assertEqual(self.db.query(StudentAddressRecord).filter_by(student_id=student_id).count(), 0)
        self.assertIsNotNone(self.db.get(Student, sibling['id']).login)
        self.assertEqual(self.db.query(StudentLogin).count(), 1)
        self.assertEqual(self.client.delete(url, params=params).status_code, 404)
        paths = self.client.app.openapi()['paths']
        for path, method in ((url, 'post'), (url, 'put'), (url, 'delete'), (url + '/admission-number', 'patch')):
            parameters = paths[path][method]['parameters']
            self.assertTrue(all(p['in'] == 'query' for p in parameters))

    def test_active_list_and_student_status(self):
        payload = nest(self.student_payload())
        url = '/schools/students'
        first = self.client.post(url, params={'school_id': 1}, json=payload).json()['data']
        second = self.client.post(url, params={'school_id': 1}, json=payload).json()['data']
        params = {'school_id': 1, 'student_id': first['id'], 'session_id': payload['student_info']['session_id']}
        response = self.client.patch(url + '/status', params=params, json={'status': False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['student_info']['status'], 'inactive')
        active = self.client.get(url, params={'school_id': 1, 'limit': 1}).json()['data']
        self.assertEqual([row['id'] for row in active], [second['id']])
        self.assertEqual(self.client.get(url, params={'school_id': 1, 'offset': 1}).json()['data'], [])
        self.assertEqual(self.db.get(Student, second['id']).status, 'active')
        self.assertEqual(self.db.get(Student, first['id']).login_id, self.db.get(Student, second['id']).login_id)
        response = self.client.patch(url + '/status', params=params, json={'status': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.client.get(url, params={'school_id': 1}).json()['data']), 2)
        for status in ('active', 'inactive', 'false', '', None, 0):
            self.assertEqual(self.client.patch(url + '/status', params=params, json={'status': status}).status_code, 422)
        self.assertEqual(self.client.patch(url + '/status', params={**params, 'school_id': 2}, json={'status': False}).status_code, 404)
        self.assertEqual(self.client.patch(url + '/status', params={'school_id': 1}, json={'status': False}).status_code, 422)
        self.assertEqual(self.client.get(url).status_code, 422)

    def test_access_and_auto_creation(self):
        payload=self.student_payload()
        Student.__table__.drop(self.engine)
        with self.engine.begin() as c:
            ensure_all_tables(c)
        url='/schools/1/students'
        for role in (UserRole.ADMIN,UserRole.SUB_ADMIN):
            self.user.role=role
            self.assertEqual(self.client.post(url,json=nest(payload)).status_code,403)


def nest(payload):
    return {name: {key: value for key, value in payload.items() if key in schema.model_fields}
            for name, schema in (("student_info", StudentInfo), ("parent_info", ParentInfo), ("present_address", StudentAddress), ("permanent_address", StudentAddress))}

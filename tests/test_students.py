import unittest
import test_sessions
from schemas.student import StudentInfo, ParentInfo, StudentAddress
from models.student import Student
from models.school import SchoolUserAssignment
from models.user import UserRole
from database.table_init import ensure_all_tables


class StudentTests(unittest.TestCase):
    setUp = test_sessions.SessionTests.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def student_payload(self):
        base='/schools/school/1'
        data={k:'Test' for k,v in {**StudentInfo.model_fields, **ParentInfo.model_fields, **StudentAddress.model_fields}.items() if v.is_required()}
        data.update(email='student@example.com',date_of_birth='2015-01-01',mobile_number='123456', father_contact_no='123456',father_aadhaar_no='123456789012',pin_code='123456')
        for field, route in [('caste_category_id','caste_categories'),('fee_category_id','fee_categories'),('class_id','classes')]:
            data[field]=self.client.post(f'{base}/{route}',json={'name':'Test'}).json()['data']['id']
        data['session_id']=self.client.post(base+'/sessions',json=self.payload).json()['data']['id']
        return data

    def test_required_only_create_edit_update(self):
        payload=self.student_payload()
        url='/schools/school/1/students'
        response=self.client.post(url,json=nest(payload))
        self.assertEqual(response.status_code,201,response.text)
        item=response.json()['data']
        self.assertIsNone(item['parent_info']['guardian_email'])
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
        url='/schools/school/1/students'
        for key,field in {**StudentInfo.model_fields, **ParentInfo.model_fields, **StudentAddress.model_fields}.items():
            if field.is_required():
                missing=dict(payload)
                del missing[key]
                self.assertEqual(self.client.post(url,json=nest(missing)).status_code,422,key)
        for field in ('class_id','session_id','fee_category_id','caste_category_id'):
            self.assertEqual(self.client.post(url,json=nest(dict(payload,**{field:99999}))).status_code,404)
        foreign=self.client.post('/schools/school/2/classes',json={'name':'Other'}).json()['data']['id']
        self.assertEqual(self.client.post(url,json=nest(dict(payload,class_id=foreign))).status_code,404)
        self.assertEqual(self.client.post(url,json=nest(dict(payload,email='bad'))).status_code,422)

    def test_access_and_auto_creation(self):
        payload=self.student_payload()
        Student.__table__.drop(self.engine)
        with self.engine.begin() as c:
            ensure_all_tables(c)
        url='/schools/school/1/students'
        for role in (UserRole.ADMIN,UserRole.SUB_ADMIN):
            self.user.role=role
            self.assertEqual(self.client.post(url,json=nest(payload)).status_code,404)
            assignment=SchoolUserAssignment(school_id=1,user_id=1,role=role.value)
            self.db.add(assignment)
            self.db.commit()
            response=self.client.post(url,json=nest(payload))
            self.assertEqual(response.status_code,201,response.text)
            sid=response.json()['data']['id']
            self.assertEqual(self.client.put(f'{url}/{sid}',json=nest(payload)).status_code,200)
            self.assertEqual(self.client.get(f'/schools/school/2/students/{sid}').status_code,404)
            self.assertEqual(self.client.put(f'/schools/school/2/students/{sid}',json=nest(payload)).status_code,404)
            self.db.delete(assignment)
            self.db.commit()
            self.assertEqual(self.client.get(f'{url}/{sid}').status_code,404)


def nest(payload):
    return {name: {key: value for key, value in payload.items() if key in schema.model_fields}
            for name, schema in (("student_info", StudentInfo), ("parent_info", ParentInfo), ("address", StudentAddress))}

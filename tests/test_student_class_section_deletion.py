import unittest

import test_students
from models.class_section import SchoolClass, Section
from models.student import Student


class StudentClassSectionDeletionTests(unittest.TestCase):
    setUp = test_students.StudentTests.setUp
    tearDown = test_students.StudentTests.tearDown
    student_payload = test_students.StudentTests.student_payload

    def test_student_references_block_both_delete_url_formats(self):
        payload = test_students.nest(self.student_payload())
        response = self.client.post('/schools/students', params={'school_id': 1}, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        student_id = response.json()['data']['id']
        info = payload['student_info']
        for resource, field, model, label in (
            ('classes', 'class_id', SchoolClass, 'Class'),
            ('sections', 'section_id', Section, 'Section'),
        ):
            record_id = info[field]
            urls = (
                f"/schools/{resource}/{record_id}?school_id=1&session_id={info['session_id']}",
                f"/schools/school/1/sessions/{info['session_id']}/{resource}/{record_id}",
            )
            for url in urls:
                response = self.client.delete(url)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()['detail'],
                                 f'{label} is assigned to students and cannot be deleted')
                self.assertIsNotNone(self.db.get(model, record_id))
                self.assertEqual(getattr(self.db.get(Student, student_id), field), record_id)
        # Removing the student reference permits deletion again.
        self.db.delete(self.db.get(Student, student_id))
        self.db.commit()
        for resource, field in (('sections', 'section_id'), ('classes', 'class_id')):
            response = self.client.delete(f"/schools/{resource}/{info[field]}",
                params={'school_id': 1, 'session_id': info['session_id']})
            self.assertEqual(response.status_code, 200, response.text)

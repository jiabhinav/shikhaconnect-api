import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import test_students
from models.student import Student
from models.student_document import StudentDocument
from models.user import UserRole


class StudentAssetTests(unittest.TestCase):
    tearDown_base = test_students.StudentTests.tearDown
    def setUp(self):
        test_students.StudentTests.setUp(self)
        payload = test_students.nest(test_students.StudentTests.student_payload(self))
        response = self.client.post('/schools/students', params={'school_id': 1}, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.student_id = response.json()['data']['id']
        self.params = {'school_id': 1, 'student_id': self.student_id,
                       'session_id': payload['student_info']['session_id']}
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch('routers.school_assets.STORAGE_ROOT', Path(self.temp.name))
        self.storage.start()

    def tearDown(self):
        self.storage.stop()
        self.temp.cleanup()
        self.tearDown_base()

    def test_profile_document_storage_and_download(self):
        stream = io.BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(stream, format='JPEG')
        response = self.client.post('/schools/students/profile', params=self.params,
                                    files={'file': ('photo.jpg', stream.getvalue(), 'image/jpeg')})
        self.assertEqual(response.status_code, 201, response.text)
        relative = response.json()['data']['profile_img']
        self.assertTrue(relative.startswith(f'1/students/{self.student_id}/profile/profile_'))
        self.assertTrue((Path(self.temp.name) / relative).is_file())
        self.assertEqual(self.db.get(Student, self.student_id).profile_img, relative)
        self.assertEqual(self.client.get('/schools/students/profile', params=self.params).status_code, 200)
        pdf = b'%PDF-1.4\nexample\n%%EOF'
        response = self.client.post('/schools/students/documents',
            params=dict(self.params, document_type='birth_certificate'), files={'file': ('birth.pdf', pdf, 'application/pdf')})
        self.assertEqual(response.status_code, 201, response.text)
        document = response.json()['data']
        self.assertTrue(document['path'].startswith(f'1/students/{self.student_id}/documents/birth_certificate_'))
        self.assertEqual(self.db.query(StudentDocument).count(), 1)
        self.assertEqual(self.client.get('/schools/students/documents', params=self.params).json()['data'], [document])
        downloaded = self.client.get(f"/schools/students/documents/{document['id']}", params=self.params)
        self.assertEqual(downloaded.content, pdf)
        self.assertEqual(self.client.get('/schools/students/profile', params=dict(self.params, school_id=2)).status_code, 404)
        self.user.role = UserRole.ADMIN
        self.assertEqual(self.client.get('/schools/students/profile', params=self.params).status_code, 403)

    def test_put_updates_existing_student_profile(self):
        stream = io.BytesIO()
        Image.new('RGB', (20, 20), 'red').save(stream, format='JPEG')
        for index in range(2):
            response = self.client.put('/schools/students/profile', params=self.params,
                files={'file': ('profile.jpg', stream.getvalue(), 'image/jpeg')})
            self.assertEqual(response.status_code, 200, response.text)
            relative = response.json()['data']['profile_img']
            self.assertEqual(self.db.get(Student, self.student_id).profile_img, relative)
            if index:
                self.assertNotEqual(relative, old_path)
                self.assertFalse((Path(self.temp.name) / old_path).exists())
            old_path = relative
        self.assertEqual(self.db.query(Student).count(), 1)
        self.assertEqual(len(list(Path(self.temp.name).rglob('profile_*.jpg'))), 1)

    def upload_profile(self):
        stream = io.BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(stream, format='JPEG')
        response = self.client.post('/schools/students/profile', params=self.params,
            files={'file': ('photo.jpg', stream.getvalue(), 'image/jpeg')})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['data']['profile_img']

    def test_delete_profile_clears_details_and_file(self):
        relative = self.upload_profile()
        response = self.client.delete('/schools/students/profile', params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], {'student_id': self.student_id, 'profile_img': ''})
        self.assertIsNone(self.db.get(Student, self.student_id).profile_img)
        self.assertFalse((Path(self.temp.name) / relative).exists())
        self.assertEqual(self.client.get('/schools/students/profile', params=self.params).status_code, 404)
        details = self.client.get(f'/schools/1/students/{self.student_id}').json()['data']
        self.assertEqual(details['student_info']['profile_img'], '')
        self.assertEqual(self.client.delete('/schools/students/profile', params=self.params).status_code, 200)

    def test_delete_profile_checks_school_and_permissions(self):
        relative = self.upload_profile()
        response = self.client.delete('/schools/students/profile', params=dict(self.params, school_id=2))
        self.assertEqual(response.status_code, 404)
        for role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            self.user.role = role
            self.assertEqual(self.client.delete('/schools/students/profile', params=self.params).status_code, 403)
        self.assertTrue((Path(self.temp.name) / relative).exists())
        self.assertEqual(self.db.get(Student, self.student_id).profile_img, relative)

    def test_delete_profile_requires_matching_session(self):
        from models.session import Session as SchoolSession
        from datetime import date
        relative = self.upload_profile()
        other = SchoolSession(school_id=1, name='Other session',
            start_date=date(2030, 4, 1), end_date=date(2031, 3, 31))
        self.db.add(other)
        self.db.commit()
        for params, expected in (
            ({key: value for key, value in self.params.items() if key != 'session_id'}, 422),
            (dict(self.params, session_id=0), 422),
            (dict(self.params, session_id=99999), 404),
            (dict(self.params, session_id=other.id), 404),
        ):
            response = self.client.delete('/schools/students/profile', params=params)
            self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(self.db.get(Student, self.student_id).profile_img, relative)
        self.assertTrue((Path(self.temp.name) / relative).exists())

    def test_delete_profile_commit_failure_preserves_file(self):
        relative = self.upload_profile()
        with patch.object(self.db, 'commit', side_effect=RuntimeError('commit failed')):
            with self.assertRaises(RuntimeError):
                self.client.delete('/schools/students/profile', params=self.params)
        self.assertEqual(self.db.get(Student, self.student_id).profile_img, relative)
        self.assertTrue((Path(self.temp.name) / relative).exists())

    def test_delete_profile_with_missing_file(self):
        relative = self.upload_profile()
        (Path(self.temp.name) / relative).unlink()
        self.assertEqual(self.client.delete('/schools/students/profile', params=self.params).status_code, 200)
        self.assertIsNone(self.db.get(Student, self.student_id).profile_img)

    def test_large_profile_saved_below_50_kb(self):
        import random
        for image_format, extension in (('PNG', 'png'), ('JPEG', 'jpg'), ('WEBP', 'webp')):
            with self.subTest(format=image_format):
                image = Image.frombytes('RGB', (600, 600), random.Random(42).randbytes(600 * 600 * 3))
                stream = io.BytesIO()
                image.save(stream, format=image_format)
                self.assertGreater(len(stream.getvalue()), 50_000)
                response = self.client.put('/schools/students/profile', params=self.params,
                    files={'file': (f'profile.{extension}', stream.getvalue())})
                self.assertEqual(response.status_code, 200, response.text)
                path = Path(self.temp.name) / response.json()['data']['profile_img']
                self.assertLess(path.stat().st_size, 50_000)
                with Image.open(path) as result:
                    result.verify()

    def test_invalid_files_do_not_write(self):
        response = self.client.post('/schools/students/profile', params=self.params,
                                    files={'file': ('bad.jpg', b'bad')})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(list(Path(self.temp.name).rglob('*')))

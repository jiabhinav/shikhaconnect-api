import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import test_teachers
from models.teacher import Teacher
from models.user import UserRole


class TeacherAssetTests(unittest.TestCase):
    create = test_teachers.TeacherTests.create
    detail_url = test_teachers.TeacherTests.detail_url
    tearDown = test_teachers.TeacherTests.tearDown
    def setUp(self):
        test_teachers.TeacherTests.setUp(self)
        self.teacher = self.create()
        self.profile_url = f"/schools/teachers/{self.teacher['id']}/profile?school_id=1"
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch('routers.school_assets.STORAGE_ROOT', Path(self.temp.name))
        self.storage.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.storage.stop)

    def upload(self, method='post', color='blue'):
        stream = io.BytesIO()
        Image.new('RGB', (20, 20), color).save(stream, format='PNG')
        return getattr(self.client, method)(self.profile_url,
            files={'file': ('photo.png', stream.getvalue(), 'image/png')})

    def test_profile_lifecycle(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()['data']
        path = Path(self.temp.name) / data['profile_img']
        self.assertTrue(path.is_file())
        self.assertEqual(self.client.get(data['profile_url']).content, path.read_bytes())
        detail = self.client.get(self.detail_url(self.teacher)).json()['data']
        self.assertEqual(detail['teacher_info']['profile_img'], data['profile_img'])
        updated = self.upload('put', 'red')
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertFalse(path.exists())
        path = Path(self.temp.name) / updated.json()['data']['profile_img']
        self.assertEqual(self.client.delete(self.profile_url).status_code, 200)
        self.assertFalse(path.exists())
        self.assertIsNone(self.db.get(Teacher, self.teacher['id']).profile_img)
        self.assertEqual(self.client.get(self.profile_url).status_code, 404)

    def test_profile_validation_and_school_scope(self):
        self.assertEqual(self.client.post(self.profile_url,
            files={'file': ('bad.png', b'bad')}).status_code, 422)
        self.assertEqual(self.client.get(self.profile_url.replace('school_id=1', 'school_id=2')).status_code, 404)
        self.user.role = UserRole.ADMIN
        self.assertEqual(self.upload().status_code, 403)

    def test_failed_update_preserves_previous_file(self):
        data = self.upload().json()['data']
        path = Path(self.temp.name) / data['profile_img']
        original = path.read_bytes()
        with patch.object(self.db, 'commit', side_effect=RuntimeError('commit failed')):
            with self.assertRaises(RuntimeError):
                self.upload('put', 'red')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.db.get(Teacher, self.teacher['id']).profile_img, data['profile_img'])
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.png'))), 1)

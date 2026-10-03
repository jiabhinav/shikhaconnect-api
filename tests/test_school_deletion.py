from datetime import date, time
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import select

from database.database import Base
from models.school import School, SchoolPermission
from models.school_assets import SchoolAssets
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.session import Session
from models.student import Student, StudentLogin, StudentAddressRecord
from models.staff import Staff, StaffAddress, StaffPermission
from models.user import LoginUser, User
from models.class_section import SchoolClass, Section
from models.caste_category import CasteCategory
from models.fee_category import FeeCategory
from models.subject import Subject
from models.stream import Stream
from models.house import House
from models.generation_settings import FeeGenerationSettings, TransportGenerationSettings
from models.timetable_settings import TimetableSettings
import test_school_creation


class SchoolDeletionTests(TestCase):
    setUp = test_school_creation.SchoolCreationTests.setUp
    tearDown = test_school_creation.SchoolCreationTests.tearDown

    def populate(self, suffix):
        payload = self.payload | {'school_info': self.payload['school_info'] | {
            'primary_email': f'school{suffix}@example.com'}}
        result = self.client.post('/super-admin/create_school', json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        school_id = result.json()['data']['id']
        session = self.db.query(Session).filter_by(school_id=school_id).one()
        values = dict(school_id=school_id, session_id=session.id, name='Test')
        caste = CasteCategory(**values)
        fee = FeeCategory(**values)
        school_class = SchoolClass(**values, class_order=1)
        section = Section(**values)
        self.db.add_all([caste, fee, school_class, section, Subject(**values),
                         Stream(**values), House(**values), SchoolAssets(school_id=school_id),
                         FeeGenerationSettings(session_id=session.id, generation_day=1, payment_due_days=5),
                         TransportGenerationSettings(session_id=session.id, generation_day=1, payment_due_day=5),
                         TimetableSettings(session_id=session.id, summer_start_time=time(8), summer_end_time=time(14),
                                           winter_start_time=time(9), winter_end_time=time(15),
                                           minimum_attendance_percentage=75, term_attendance_enabled=False)])
        account = LoginUser(first_name='Staff', email=f'staff{suffix}@example.com',
                            mobile=f'123456{suffix}', password='test', role='Teacher')
        student_login = StudentLogin(school_id=school_id, mobile='123456', password='test')
        self.db.add_all([account, student_login])
        self.db.flush()
        self.db.add_all([Staff(school_id=school_id, login_user_id=account.id, caste_category_id=caste.id),
                         StaffAddress(login_user_id=account.id),
                         StaffPermission(login_user_id=account.id, staff_module_id=1)])
        student = Student(school_id=school_id, login_id=student_login.id, session_id=session.id,
                          caste_category_id=caste.id, fee_category_id=fee.id,
                          class_id=school_class.id, section_id=section.id, date_of_birth=date(2015, 1, 1),
                          first_name='Test', last_name='Student', mobile_number='123456', gender='Female',
                          email='student@example.com', nationality='Indian', father_name='Father', mother_name='Mother',
                          line_1='Street', city='Delhi', country='India', state='Delhi')
        self.db.add(student)
        self.db.flush()
        self.db.add(StudentAddressRecord(student_id=student.id, address_type='present', line_1='Street',
                                         city='Delhi', state='Delhi', country='India'))
        self.db.commit()
        return school_id, account.id

    def test_deletes_all_school_owned_records_preserving_other_school(self):
        for prefix in ('/super-admin', '/schools'):
            with self.subTest(prefix=prefix):
                school_id, account_id = self.populate(prefix + 'target')
                other_id, other_account = self.populate(prefix + 'other')
                admin = LoginUser(first_name='Admin', email=f'admin{school_id}@example.com',
                                  mobile=f'admin{school_id}', password='test')
                self.db.add(admin)
                self.db.flush()
                self.db.add_all([User(login_user_id=admin.id),
                                 SchoolMapping(user_id=admin.id, school_id=school_id, status=SchoolMappingStatus.ACTIVE),
                                 SchoolMapping(user_id=admin.id, school_id=other_id, status=SchoolMappingStatus.ACTIVE)])
                self.db.commit()
                admin_id = admin.id
                before = {t.name: self.db.execute(select(t)).all() for t in Base.metadata.sorted_tables}
                response = self.client.delete(f'{prefix}/delete_school/{school_id}')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIsNone(self.db.get(School, school_id))
                self.assertIsNone(self.db.get(LoginUser, account_id))
                self.assertIsNotNone(self.db.get(LoginUser, admin_id))
                self.assertIsNotNone(self.db.get(LoginUser, other_account))
                for table in Base.metadata.sorted_tables:
                    if 'school_id' in table.c:
                        self.assertEqual(self.db.execute(select(table).where(table.c.school_id == school_id)).all(), [])
                        self.assertEqual(self.db.execute(select(table).where(table.c.school_id == other_id)).all(),
                                         [r for r in before[table.name] if r._mapping['school_id'] == other_id])
                for model in (StudentAddressRecord, StaffAddress, StaffPermission,
                              FeeGenerationSettings, TransportGenerationSettings, TimetableSettings):
                    self.assertEqual(self.db.query(model).count(), len(before[model.__tablename__]) - 1)

    def test_failure_rolls_back_all_deletions(self):
        school_id, account_id = self.populate('rollback')
        before = {t.name: self.db.execute(select(t)).all() for t in Base.metadata.sorted_tables}
        with patch.object(self.db, 'commit', side_effect=RuntimeError('Commit failed')):
            response = self.client.delete(f'/super-admin/delete_school/{school_id}')
        self.assertEqual(response.status_code, 500)
        for table in Base.metadata.sorted_tables:
            self.assertEqual(self.db.execute(select(table)).all(), before[table.name])

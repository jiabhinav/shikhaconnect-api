import unittest
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from openpyxl import Workbook

import catalog_setup
import test_sessions
from models.class_section import SchoolClass, Section
from models.generation_settings import FeeGenerationSettings
from models.student import Student
from models.student_carry_forward import StudentCarryForward
from schemas.enums import FeeType, LateFeeType


class StudentCarryForwardBulkTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown

    def test_payment_can_clear_carry_forward_before_fee_heads_are_configured(self):
        student_id = self.make_student(1, 1, '001')
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal('100')
        self.db.commit()
        scope = {'school_id': 1, 'session_id': 1}
        payment = {'student_id': student_id, 'payment_mode': 'Cash',
                   'months': [{'fee_month': '2025-01', 'payment_amount': '35.00'}]}
        for payload in (payment, payment | {'cheque_number': ''}):
            response = self.client.post('/schools/student_fees', params=scope, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()['carry_forward_paid_amount'], '35.00')
            self.assertEqual(response.json()['data'], [])
        balance = self.client.get('/schools/fee_payments', params=scope | {'student_id': student_id}).json()['data'][0]
        self.assertEqual(balance['carry_forward']['due_amount'], '30.00')

    def test_fee_payments_calculates_daily_and_flat_monthly_late_fees(self):
        student_id = self.make_student(1, 1, '001')
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal('100')
        settings = FeeGenerationSettings(
            school_id=1, session_id=1, fee_type=FeeType.SCHOOL_FEE, generation_day=1,
            payment_due_day=9, late_fee_enabled=True, late_fee_type=LateFeeType.DAILY,
            late_fee_amount=Decimal('5.50'), min_due_amount=25,
        )
        self.db.add(settings)
        self.db.commit()

        def balance_on(day, fee_month='2025-01'):
            params = {'school_id': 1, 'session_id': 1, 'student_id': student_id}
            if fee_month is not None:
                params['fee_month'] = fee_month
            with patch('routers.fee_assignments.datetime') as clock:
                clock.now.return_value = datetime(2025, 1, day)
                response = self.client.get('/schools/fee_payments', params=params)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        above_threshold = balance_on(13)['late_fee']
        self.assertEqual(above_threshold['amount'], '16.50')
        self.assertNotIn('due_percentage', above_threshold)
        carry.paid_amount = Decimal('75')
        self.db.commit()
        self.assertEqual(balance_on(10)['late_fee']['amount'], '0.00')
        overdue = balance_on(13)
        self.assertEqual(overdue['late_fee'], {
            'enabled': True, 'type': 'daily', 'due_date': '2025-01-10', 'overdue_days': 3, 'amount': '16.50',
            'late_fee_amount': '5.50', 'min_due_percentage': 25, 'fee_month': '2025-01',
            'paid_amount': '0.00', 'due_amount': '16.50',
        })
        self.assertEqual(overdue['data'][0]['due_amount'], '25.00')
        self.assertNotIn('late_fee', overdue['data'][0])
        self.assertEqual(balance_on(13, fee_month=None)['late_fee'], overdue['late_fee'])
        self.assertEqual(balance_on(13, fee_month='2025-02')['late_fee'], overdue['late_fee'])

        settings.late_fee_type = LateFeeType.MONTHLY
        self.db.commit()
        self.assertEqual(balance_on(11)['late_fee']['amount'], '5.50')
        self.assertEqual(balance_on(30)['late_fee']['amount'], '5.50')

        carry.paid_amount = Decimal('76')
        self.db.commit()
        self.assertEqual(balance_on(30)['late_fee']['amount'], '0.00')
        carry.paid_amount = Decimal('100')
        self.db.commit()
        self.assertEqual(balance_on(30)['late_fee']['amount'], '0.00')

    def make_student(self, school_id, session_id, admission_number):
        school_class = self.db.query(SchoolClass).filter_by(school_id=school_id, session_id=session_id).first()
        if school_class is None:
            school_class = SchoolClass(
                school_id=school_id, session_id=session_id, name='Test', class_order=1,
            )
            self.db.add(school_class)
            self.db.flush()
        student = Student(
            school_id=school_id, session_id=session_id, class_id=school_class.id,
            admission_number=admission_number, first_name='Student', mobile_number='123456',
            gender='Female', nationality='Indian', father_name='Father', mother_name='Mother',
            line_1='Street', city='Delhi', country='India', state='Delhi',
        )
        self.db.add(student)
        self.db.commit()
        return student.id

    def test_bulk_upload_updates_scoped_balances_and_rejects_invalid_batch(self):
        student_id = self.make_student(1, 1, '00123')
        other_student_id = self.make_student(2, 2, '00123')
        second_student_id = self.make_student(1, 3, '00456')
        url = '/schools/student_carry_forwards/bulk-upload/json?school_id=1&session_id=1'
        original = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        original.amount = Decimal('50.00')
        original.paid_amount = Decimal('25.00')
        self.db.commit()
        original_id = original.id

        payload = {'rows': [{'admission_number': '00123', 'amount': '125.50'}]}
        for _ in range(2):
            response = self.client.post(url, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['month'], '2025-01')
            self.assertEqual(response.json()['data'], [{
                'id': original_id, 'student_id': student_id, 'admission_number': '00123',
                'amount': '125.50', 'paid_amount': '25.00', 'unpaid_amount': '100.50',
            }])

        invalid = {'rows': [{'admission_number': '00123', 'amount': 200},
                            {'admission_number': '00456', 'amount': 50}]}
        response = self.client.post(url, json=invalid)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()['detail']['admission_numbers'], ['00456'])
        self.assertEqual(self.client.post(url, json={'rows': payload['rows'] * 2}).status_code, 422)
        self.assertEqual(self.client.post(url, json={
            'rows': [{'admission_number': '00123', 'amount': 24}],
        }).status_code, 409)
        self.assertEqual(self.db.get(StudentCarryForward, original_id).amount, Decimal('125.50'))
        for excluded_id in (other_student_id, second_student_id):
            record = self.db.query(StudentCarryForward).filter_by(student_id=excluded_id).one()
            self.assertEqual(record.amount, 0)

        payment = self.client.get('/schools/fee_payments', params={
            'school_id': 1, 'session_id': 1, 'student_id': student_id, 'fee_month': '2025-01',
        })
        self.assertEqual(payment.status_code, 200, payment.text)
        self.assertEqual(Decimal(str(payment.json()['data'][0]['carry_forward']['due_amount'])), Decimal('100.50'))

    def test_bulk_upload_creates_missing_rows(self):
        student_id = self.make_student(1, 1, 'A')
        school_class = self.db.get(Student, student_id).class_id
        second = Student(
            school_id=1, session_id=1, class_id=school_class,
            admission_number='B', first_name='Student', mobile_number='123457',
            gender='Female', nationality='Indian', father_name='Father', mother_name='Mother',
            line_1='Street', city='Delhi', country='India', state='Delhi',
        )
        self.db.add(second)
        self.db.commit()
        second_id = second.id
        self.db.query(StudentCarryForward).filter_by(student_id=student_id).delete()
        self.db.commit()

        response = self.client.post('/schools/student_carry_forwards/bulk-upload/json',
                                    params={'school_id': 1, 'session_id': 1}, json={'rows': [
            {'admission_number': 'B', 'amount': 70},
            {'admission_number': 'A', 'amount': 50},
        ]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 2)
        self.assertEqual([row['student_id'] for row in response.json()['data']], [second_id, student_id])
        stored = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        self.assertEqual((stored.month, stored.amount, stored.paid_amount), ('2025-01', Decimal('50.00'), 0))

    def upload_excel(self, rows):
        stream = BytesIO()
        workbook = Workbook()
        workbook.active.append([
            'Serial Number', 'Admission Number', 'Student Name', 'Class–Section',
            'Father’s Name', 'Carry Forward Amount',
        ])
        for row in rows:
            workbook.active.append(row)
        workbook.save(stream)
        workbook.close()
        return self.client.post(
            '/schools/student_carry_forwards/bulk-upload',
            params={'school_id': 1, 'session_id': 1},
            files={'file': ('carry-forward.xlsx', stream.getvalue())},
        )

    def test_raw_upload_accepts_array_and_replaces_existing_amount(self):
        first = self.make_student(1, 1, '004003')
        second = self.make_student(1, 1, '3762')
        url = '/schools/student_carry_forwards/bulk-upload/raw'
        params = {'school_id': 1, 'session_id': 1}
        rows = [{'admission_number': '004003', 'amount': '125.50'}, {'admission_id': 3762, 'amount': 80}]
        response = self.client.post(url, params=params, json=rows)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row['student_id'] for row in response.json()['data']], [first, second])
        duplicate = self.client.post(url, params=params, json=rows + [{'admission_number': '3762', 'amount': 10}])
        self.assertEqual(duplicate.status_code, 422, duplicate.text)
        self.assertEqual(self.client.post(url, params=params, json={'rows': rows}).status_code, 422)

        replacement = self.client.post(url, params=params, json=[{'admission_number': '004003', 'amount': 50}])
        self.assertEqual(replacement.status_code, 200, replacement.text)
        self.assertEqual(replacement.json()['data'][0]['id'], response.json()['data'][0]['id'])
        self.assertEqual(Decimal(replacement.json()['data'][0]['amount']), Decimal('50.00'))

    def test_excel_upload_matches_template_and_skips_blank_amounts(self):
        schema = self.client.app.openapi()
        operation = schema['paths']['/schools/student_carry_forwards/bulk-upload']['post']
        self.assertEqual(set(operation['requestBody']['content']), {'multipart/form-data'})
        first = self.make_student(1, 1, '004003')
        second = self.make_student(1, 1, '3762')
        response = self.upload_excel([
            [1, '004003', 'Student A', 'PG-A', 'Parent A', 125.50],
            [2, 3762, 'Student B', 'PG-A', 'Parent B', 0],
            [3, 3764, 'Student C', 'PG-A', 'Parent C', None],
        ])
        self.assertEqual(response.status_code, 200, response.text)
        records = response.json()['data']
        self.assertEqual([row['student_id'] for row in records], [first, second])
        self.assertEqual([row['admission_number'] for row in records], ['004003', '3762'])
        self.assertEqual([Decimal(row['amount']) for row in records], [Decimal('125.50'), Decimal(0)])

        response = self.upload_excel([
            [1, '004003', 'Student A', 'PG-A', 'Parent A', 999],
            [2, 3762, 'Student B', 'PG-A', 'Parent B', '=1+1'],
        ])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()['detail']['row'], 3)
        self.assertEqual(self.db.query(StudentCarryForward).filter_by(student_id=first).one().amount, Decimal('125.50'))

    def test_list_carry_forwards_filters_class_and_section_and_returns_payment_status(self):
        section = Section(school_id=1, session_id=1, name='A')
        other_section = Section(school_id=1, session_id=1, name='B')
        self.db.add_all([section, other_section])
        self.db.flush()

        for admission, name, paid, selected_section in (
            ('001', 'Zoya', '0.00', section),
            ('002', 'Amir', '25.00', section),
            ('003', 'Ben', '100.00', section),
            ('004', 'Excluded', '0.00', other_section),
        ):
            student = self.db.get(Student, self.make_student(1, 1, admission))
            student.first_name = name
            student.section_id = selected_section.id
            record = self.db.query(StudentCarryForward).filter_by(student_id=student.id).one()
            record.amount = Decimal('100.00')
            record.paid_amount = Decimal(paid)
            self.db.commit()

        url = '/schools/student_carry_forwards'
        params = {'school_id': 1, 'session_id': 1, 'class_id': student.class_id, 'section_id': section.id}
        response = self.client.get(url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['total'], 3)
        self.assertEqual(
            [(row['serial_number'], row['student_name'], row['status'], row['due_amount'])
             for row in result['data']],
            [(1, 'Amir', 'PARTIAL', '75.00'), (2, 'Ben', 'PAID', '0.00'), (3, 'Zoya', 'UNPAID', '100.00')],
        )
        self.assertEqual(result['data'][1]['amount'], '100.00')
        self.assertTrue(all(row['class_name'] == 'Test' and row['section_name'] == 'A' for row in result['data']))
        empty = self.client.get(url, params=dict(params, section_id=999))
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()['data'], [])
        self.assertEqual(self.client.get(url, params=dict(params, session_id=2)).status_code, 404)

        unassigned_id = self.make_student(1, 1, '005')
        self.db.query(StudentCarryForward).filter_by(student_id=unassigned_id).delete()
        self.db.commit()
        zero_amount_id = self.make_student(1, 1, '008')
        self.make_student(1, 3, '006')
        scope = {'school_id': 1, 'session_id': 1}
        response = self.client.get(url, params=scope)
        self.assertEqual(response.status_code, 200, response.text)
        unfiltered = response.json()
        self.assertEqual(unfiltered['total'], 4)
        self.assertEqual(unfiltered['total_records'], 4)
        listed_ids = {row['student_id'] for row in unfiltered['data']}
        self.assertNotIn(unassigned_id, listed_ids)
        self.assertNotIn(zero_amount_id, listed_ids)

        for filters, names in (
            ({'class_id': params['class_id']}, ['Amir', 'Ben', 'Zoya', 'Excluded']),
            ({'section_id': section.id}, ['Amir', 'Ben', 'Zoya']),
            ({'payment_status': 'UNPAID'}, ['Zoya', 'Excluded']),
            ({'payment_status': 'PARTIAL', 'search': 'AMi'}, ['Amir']),
            ({'payment_status': 'PAID', 'search': 'AMi'}, []),
        ):
            result = self.client.get(url, params=scope | filters)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([row['student_name'] for row in result.json()['data']], names)
            self.assertEqual(result.json()['total_records'], 4)

        earlier_student = self.db.get(Student, self.make_student(1, 1, '007'))
        self.db.get(SchoolClass, params['class_id']).class_order = 2
        earlier_class = SchoolClass(school_id=1, session_id=1, name='Z Class', class_order=1)
        self.db.add(earlier_class)
        self.db.flush()
        earlier_student.class_id = earlier_class.id
        earlier_student.first_name = 'Zara'
        self.db.query(StudentCarryForward).filter_by(student_id=earlier_student.id).one().amount = Decimal('10.00')
        self.db.commit()

        result = self.client.get(url, params=scope)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(
            [row['student_name'] for row in result.json()['data']],
            ['Zara', 'Amir', 'Ben', 'Zoya', 'Excluded'],
        )

import unittest
from datetime import datetime, timezone
from decimal import Decimal

import catalog_setup
import test_sessions
import test_student_carry_forwards
from models.fee_head import FeeHead
from models.class_section import Section
from models.student import Student
from models.student_carry_forward import StudentCarryForward
from models.student_fee import StudentFee, StudentFeeHead


class StudentFeeDiscountTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown
    make_student = test_student_carry_forwards.StudentCarryForwardBulkTests.make_student

    def test_student_list_filters_and_paginates_within_school_session(self):
        selected = self.db.get(Student, self.make_student(1, 1, '004003'))
        other = self.db.get(Student, self.make_student(1, 1, '3762'))
        self.make_student(2, 2, '004003')
        self.make_student(1, 3, '004003')
        section = Section(school_id=1, session_id=1, name='A')
        self.db.add(section)
        self.db.flush()
        selected.first_name, selected.last_name = 'Aayat', 'Arbeea'
        selected.section_id = section.id
        other.first_name, other.status = 'Zoya', 'inactive'
        self.db.commit()

        url = '/schools/student_fee_adjustments/students'
        scope = {'school_id': 1, 'session_id': 1}
        response = self.client.get(url, params=scope)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 2)
        self.assertEqual([row['student_id'] for row in response.json()['data']], [selected.id, other.id])
        self.assertTrue(all(row['discount']['count'] == row['penalty']['count'] == 0 for row in response.json()['data']))

        for filters in (
            {'search': 'aayat arb'},
            {'search': '004003'},
            {'class_id': selected.class_id, 'section_id': section.id, 'status': 'active'},
            {'student_id': selected.id},
        ):
            result = self.client.get(url, params=scope | filters)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['total'], 1)
            self.assertEqual(result.json()['data'][0]['student_id'], selected.id)
            self.assertEqual(result.json()['data'][0]['section_name'], 'A')

        page = self.client.get(url, params=scope | {'offset': 1, 'limit': 1})
        self.assertEqual(page.json()['total'], 2)
        self.assertEqual(page.json()['data'][0]['student_id'], other.id)
        self.assertEqual(self.client.get(url, params=scope | {'session_id': 2}).status_code, 404)

        carry = self.db.query(StudentCarryForward).filter_by(student_id=selected.id).one()
        carry.amount = Decimal('100.00')
        self.db.commit()
        adjustments_url = '/schools/student_fee_adjustments'
        adjustment = {'frequency': 'One Time', 'date': '2025-01', 'amount': 10}
        saved = {}
        for student, payload in (
            (selected, dict(adjustment, type='Discount', discount_type='Charity')),
            (selected, dict(adjustment, type='Discount', discount_type='Sibling')),
            (other, dict(adjustment, type='Penalty')),
        ):
            created = self.client.post(adjustments_url, params=scope | {'student_id': student.id}, json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            saved.setdefault(student.id, []).append(created.json()['data'])

        for filters, expected in (
            ({'type': 'Discount', 'class_id': selected.class_id, 'section_id': section.id, 'search': 'aayat'},
             [selected.id]),
            ({'type': 'Penalty'}, [other.id]),
            ({'type': 'Penalty', 'section_id': section.id}, []),
            ({'type': 'All', 'class_id': 'all', 'section_id': 'ALL'}, [selected.id, other.id]),
            ({'type': 'all', 'section_id': section.id}, [selected.id]),
            ({}, [selected.id, other.id]),
        ):
            filtered = self.client.get(url, params=scope | filters)
            self.assertEqual(filtered.status_code, 200, filtered.text)
            self.assertEqual(filtered.json()['total'], len(expected))
            self.assertEqual([row['student_id'] for row in filtered.json()['data']], expected)
            for row in filtered.json()['data']:
                self.assertNotIn('adjustments', row)
                for kind in ('discount', 'penalty'):
                    matches = [item for item in saved[row['student_id']] if item['type'].lower() == kind]
                    amount = f'{len(matches) * 10:.2f}'
                    self.assertEqual(row[kind], {'count': len(matches), 'total_amount': amount})
                    self.assertEqual(row[f'total_{kind}_amount'], amount)
        self.assertEqual(self.client.get(url, params=scope | {'class_id': 0}).status_code, 422)

        penalty = self.client.post(
            adjustments_url, params=scope | {'student_id': selected.id}, json=dict(adjustment, type='Penalty'),
        )
        self.assertEqual(penalty.status_code, 201, penalty.text)
        filtered = self.client.get(url, params=scope | {'type': 'Discount'})
        summary = filtered.json()['data'][0]
        self.assertEqual(summary['total_discount_amount'], '20.00')
        self.assertEqual(summary['total_penalty_amount'], '10.00')

        detail_url = f'{url}/adjustments'
        detail_scope = scope | {'student_id': selected.id}
        for kind, expected in (
            ('Discount', saved[selected.id]),
            ('Penalty', [penalty.json()['data']]),
            ('All', [*saved[selected.id], penalty.json()['data']]),
        ):
            details = self.client.get(detail_url, params=detail_scope | {'type': kind})
            self.assertEqual(details.status_code, 200, details.text)
            self.assertEqual(details.json()['data'], expected)
        page = self.client.get(detail_url, params=detail_scope | {'type': 'Discount', 'offset': 1, 'limit': 1})
        self.assertEqual(page.json()['total'], 2)
        self.assertEqual(page.json()['data'], saved[selected.id][1:])

    def test_bulk_create_uses_single_insert_rules_and_rolls_back_invalid_batch(self):
        student_id = self.make_student(1, 1, '001')
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal('50.00')
        self.db.commit()
        scope = {'school_id': 1, 'session_id': 1, 'student_id': student_id}
        url = '/schools/student_fee_adjustments'
        penalty = {'type': 'Penalty', 'frequency': 'One Time', 'date': '2025-01', 'amount': 10}
        discount = dict(penalty, type='Discount', discount_type='Charity', frequency='Monthly', amount=40)
        batch = [penalty, discount, dict(discount, frequency='One Time', amount=21)]

        rejected = self.client.post(f'{url}/bulk-upload', params=scope, json=batch)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()['detail']['row'], 3)
        self.assertEqual(rejected.json()['detail']['due_amount'], '20.00')
        self.assertEqual(self.client.get(url, params=scope).json()['total'], 0)

        created = self.client.post(
            f'{url}/bulk-upload', params=scope, json=[*batch[:2], dict(batch[2], amount=20)],
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['total'], 3)
        first, second, third = created.json()['data']
        self.assertEqual(first['data'][0]['type'], 'Penalty')
        self.assertEqual(second['data'][0]['type'], 'Discount')
        self.assertEqual(second['skipped_months'][0]['fee_month'], '2025-02')
        self.assertIsNotNone(third['data'][0]['applied_at'])
        self.assertEqual(self.client.get(url, params=scope).json()['total'], 3)

        page = self.client.get(url, params=scope | {'offset': 1, 'limit': 1})
        self.assertEqual(page.status_code, 200, page.text)
        result = page.json()
        self.assertEqual((result['total'], result['offset'], result['limit']), (3, 1, 1))
        self.assertEqual([row['id'] for row in result['data']], [second['data'][0]['id']])
        empty = self.client.get(url, params=scope | {'offset': 3, 'limit': 1})
        self.assertEqual(empty.json()['total'], 3)
        self.assertEqual(empty.json()['data'], [])
        self.assertEqual(self.client.get(url, params=scope | {'limit': 201}).status_code, 422)

    def test_discount_checks_carry_forward_fee_heads_and_existing_discounts(self):
        student_id = self.make_student(1, 1, '001')
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal('60.00')
        carry.paid_amount = Decimal('40.00')
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-01',
            total_amount=150, net_amount=150, paid_amount=100, status='PARTIAL',
            fee_heads=[
                StudentFeeHead(
                    fee_head=FeeHead(school_id=1, session_id=1, fee_head_name='Tuition', student_type='New'),
                    amount=100, net_amount=100, paid_amount=100,
                ),
                StudentFeeHead(
                    fee_head=FeeHead(school_id=1, session_id=1, fee_head_name='Books', student_type='New'),
                    amount=50, net_amount=50, paid_amount=0,
                ),
            ],
        ))
        self.db.commit()

        scope = {'school_id': 1, 'session_id': 1, 'student_id': student_id}
        url = '/schools/student_fee_adjustments'
        payload = {
            'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
            'date': '2025-01',
        }

        too_high = self.client.post(url, params=scope, json=dict(payload, amount='70.01'))
        self.assertEqual(too_high.status_code, 422, too_high.text)
        self.assertEqual(too_high.json()['detail']['due_amount'], '70.00')
        monthly = self.client.post(url, params=scope, json=dict(payload, amount=71, frequency='Monthly'))
        self.assertEqual(monthly.status_code, 422, monthly.text)
        self.assertEqual(monthly.json()['detail']['skipped_months'][0]['fee_month'], '2025-01')
        self.assertEqual(self.client.get(url, params=scope).json()['total'], 0)

        before = datetime.now(timezone.utc)
        first = self.client.post(url, params=scope, json=dict(payload, amount=40))
        after = datetime.now(timezone.utc)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()['message'], 'Discount applied successfully')
        self.assertEqual(first.json()['data']['date'], '2025-01-01')
        self.assertEqual(first.json()['data']['description'], '')
        applied_at = first.json()['data']['applied_at']
        applied_time = datetime.fromisoformat(applied_at)
        if applied_time.tzinfo is None:
            applied_time = applied_time.replace(tzinfo=timezone.utc)
        self.assertLessEqual(before, applied_time)
        self.assertLessEqual(applied_time, after)
        adjustment_id = first.json()['data']['id']
        rejected = self.client.post(url, params=scope, json=dict(payload, amount=31))
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()['detail']['due_amount'], '30.00')

        invalid_revision = self.client.put(f'{url}/{adjustment_id}', params=scope, json=dict(payload, amount=71))
        self.assertEqual(invalid_revision.status_code, 422, invalid_revision.text)
        revision = self.client.put(
            f'{url}/{adjustment_id}', params=scope, json=dict(payload, amount=70, description='Approved discount'),
        )
        self.assertEqual(revision.status_code, 200, revision.text)
        revision = self.client.put(f'{url}/{adjustment_id}', params=scope, json=dict(payload, amount=70))
        self.assertEqual(revision.status_code, 200, revision.text)
        self.assertEqual(revision.json()['data']['description'], 'Approved discount')
        payment = self.client.get('/schools/fee_payments', params=dict(scope, fee_month='2025-01'))
        self.assertEqual(payment.status_code, 200, payment.text)
        self.assertEqual(payment.json()['data'][0]['due_amount'], '0.00')
        blocked = self.client.post(url, params=scope, json=dict(payload, amount=1, date='2025-01-20'))
        self.assertEqual(blocked.status_code, 422, blocked.text)
        self.assertEqual(blocked.json()['detail']['message'], 'All fees are paid for this month')
        marked = self.client.post(f'{url}/{adjustment_id}/mark_applied', params=scope)
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual(marked.json()['message'], 'Discount applied successfully')
        self.assertEqual(marked.json()['data']['applied_at'], applied_at)

        payment_record = self.db.query(StudentFee).filter_by(student_id=student_id).one()
        payment_record.discount_amount = Decimal('1.00')
        self.db.commit()
        self.assertEqual(self.client.put(f'{url}/{adjustment_id}', params=scope, json=dict(payload, amount=65)).status_code, 409)
        self.assertEqual(self.client.delete(f'{url}/{adjustment_id}', params=scope).status_code, 409)
        payment_record.discount_amount = Decimal('0.00')
        self.db.commit()
        self.assertEqual(self.client.delete(f'{url}/{adjustment_id}', params=scope).status_code, 204)

        for adjustment_date in ('2025-03-10', '2025-01-15', '2025-02-20'):
            penalty = {
                'type': 'Penalty', 'frequency': 'One Time',
                'amount': 15 if adjustment_date.startswith('2025-03') else 10,
                'date': adjustment_date, 'description': None,
            }
            result = self.client.post(url, params=scope, json=penalty)
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(result.json()['data']['description'], '')
        dates = [row['date'] for row in self.client.get(url, params=scope).json()['data']]
        self.assertEqual(dates, ['2025-01-15', '2025-02-20', '2025-03-10'])

        monthly = self.client.post(url, params=scope, json=dict(payload, amount=15, frequency='Monthly'))
        self.assertEqual(monthly.status_code, 201, monthly.text)
        skipped = {month['fee_month']: month for month in monthly.json()['skipped_months']}
        self.assertEqual(skipped['2025-02']['due_amount'], '10.00')
        self.assertEqual(skipped['2025-04']['due_amount'], '0.00')
        adjustments = self.client.get(url, params=scope).json()['data']
        self.assertEqual(
            [row['date'] for row in adjustments if row['type'] == 'Discount'],
            ['2025-01-01', '2025-03-01'],
        )

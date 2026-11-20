import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

import catalog_setup
import test_sessions
import test_student_carry_forwards
from models.fee_head import FeeHead
from models.fee_assignment import FeeAssignment
from models.fee_category import FeeCategory
from models.fee_configuration import FeeConfigurationHead
from models.fee_structure import FeeStructure
from models.generation_settings import FeeGenerationSettings
from models.class_section import Section
from models.student import Student
from models.student_carry_forward import StudentCarryForward
from models.student_fee import StudentFee, StudentFeeHead
from models.student_fee_adjustment import StudentFeeAdjustment
from models.session import Session as SchoolSession


class StudentFeeDiscountTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown
    make_student = test_student_carry_forwards.StudentCarryForwardBulkTests.make_student

    def test_cash_payment_clears_carry_forward_fee_heads_and_penalty(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        student = self.db.get(Student, student_id)
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal('200')
        structure = FeeStructure(**scope, fee_structure_name='School Fees')
        category = FeeCategory(**scope, name='Regular')
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        self.db.add_all([structure, category, tuition])
        self.db.flush()
        configuration = FeeConfigurationHead(
            **scope, configuration_id=1, fee_structure_id=structure.id, fee_category_id=category.id,
            fee_head_id=tuition.id, base_amount=5600,
        )
        self.db.add(configuration)
        self.db.flush()
        self.db.add(FeeAssignment(**scope, class_id=student.class_id, fee_configure_id=configuration.id))
        self.db.add(StudentFeeAdjustment(
            **scope, student_id=student_id, date=date(2025, 1, 1),
            type='Penalty', frequency='One Time', amount=100, description='',
        ))
        self.db.add(FeeGenerationSettings(
            **scope, generation_day=1, payment_due_day=10, late_fee_enabled=True,
            late_fee_type='monthly', late_fee_amount=1120, min_due_amount=1,
        ))
        self.db.commit()
        url = '/schools/student_fees'
        student_scope = scope | {'student_id': student_id}
        payload = {'student_id': student_id, 'payment_mode': 'Cash',
                   'months': [{'fee_month': '2025-01', 'payment_amount': 5900}],
                   'late_fee_payment_amount': 1000}
        premature = self.client.post(url, params=scope, json=payload | {'months': []})
        self.assertEqual(premature.status_code, 422, premature.text)
        response = self.client.post(url, params=scope, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(Decimal(result['carry_forward_paid_amount']), Decimal('200'))
        self.assertEqual(Decimal(result['penalty_paid_amount']), Decimal('100'))
        self.assertEqual(Decimal(result['late_fee_paid_amount']), Decimal('1000'))
        self.assertEqual(Decimal(result['data'][0]['paid_amount']), Decimal('5600'))
        balance = self.client.get('/schools/fee_payments', params=student_scope | {'fee_month': '2025-01'})
        month = balance.json()['data'][0]
        self.assertEqual((month['paid_amount'], month['due_amount'], month['status']), ('5900.00', '0.00', 'PAID'))
        self.assertEqual(month['fee_adjustment'][0]['due_amount'], '0.00')
        self.assertEqual(balance.json()['late_fee']['due_amount'], '120.00')
        remainder = self.client.post(
            url, params=scope, json=payload | {
                'payment_mode': 'Cheque', 'cheque_number': '00120', 'months': [], 'late_fee_payment_amount': 120,
            },
        )
        self.assertEqual(remainder.status_code, 201, remainder.text)
        cheque = remainder.json()['data']
        self.assertEqual(cheque['status'], 'PENDING')
        self.assertEqual(
            self.client.get('/schools/fee_payments', params=student_scope).json()['late_fee']['due_amount'], '120.00',
        )
        cleared = self.client.patch(
            f"{url}/cheque_payments/{cheque['id']}", params=scope, json={'status': 'SUCCESS'},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        settled = self.client.get('/schools/fee_payments', params=student_scope).json()['late_fee']
        self.assertEqual((settled['paid_amount'], settled['due_amount']), ('1120.00', '0.00'))
        self.assertEqual(self.client.post(url, params=scope, json=payload).status_code, 422)

    def test_cheque_payment_waits_for_clearance_and_posts_only_once(self):
        student_id = self.make_student(1, 1, '001')
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-01',
            total_amount=100, net_amount=100, paid_amount=0, status='UNPAID',
            fee_heads=[
                StudentFeeHead(
                    fee_head=FeeHead(school_id=1, session_id=1, fee_head_name='Tuition', student_type='New'),
                    amount=100, net_amount=100, paid_amount=0,
                ),
            ],
        ))
        self.db.commit()
        scope = {'school_id': 1, 'session_id': 1}
        student_scope = scope | {'student_id': student_id}
        url = '/schools/student_fees'
        adjustments_url = '/schools/student_fee_adjustments'
        adjustment_ids = []
        for adjustment in (
            {'type': 'Discount', 'discount_type': 'Charity'},
            {'type': 'Penalty'},
        ):
            body = dict(adjustment, frequency='One Time', date='2025-01-15', amount=10)
            created = self.client.post(adjustments_url, params=student_scope, json=body)
            self.assertEqual(created.status_code, 201, created.text)
            adjustment_ids.append((created.json()['data']['id'], body))
        payload = {
            'student_id': student_id, 'payment_mode': 'Cheque',
            'months': [{'fee_month': '2025-01', 'payment_amount': 70}],
        }
        for invalid in (payload, payload | {'cheque_number': '   '}):
            rejected = self.client.post(url, params=scope, json=invalid)
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual(rejected.json()['detail'][0]['msg'], 'Enter the cheque number.')
        response = self.client.post(url, params=scope, json=payload | {'cheque_number': '000123'})
        self.assertEqual(response.status_code, 201, response.text)
        cheque = response.json()['data']
        self.assertEqual((cheque['status'], cheque['cheque_number']), ('PENDING', '000123'))
        self.assertEqual(Decimal(cheque['payment_amount']), Decimal('70'))
        self.assertEqual(self.client.get(url, params=student_scope).json()['data'][0]['paid_amount'], '0.00')
        before = self.client.get(adjustments_url, params=student_scope).json()['data']
        self.assertTrue(all(row['editable'] for row in before))

        list_url = f'{url}/cheque_payments'
        pending = self.client.get(list_url, params=student_scope | {'status': 'PENDING'})
        self.assertEqual(pending.json()['data'], [cheque])
        retry = self.client.post(url, params=scope, json=payload | {'cheque_number': '000124'})
        self.assertEqual(retry.status_code, 201, retry.text)
        second_url = f"{list_url}/{retry.json()['data']['id']}"
        for _ in range(2):
            cleared = self.client.patch(f"{list_url}/{cheque['id']}", params=scope, json={'status': 'SUCCESS'})
            self.assertEqual(cleared.status_code, 200, cleared.text)
            self.assertEqual(cleared.json()['data']['status'], 'SUCCESS')
        excess = self.client.patch(second_url, params=scope, json={'status': 'SUCCESS'})
        self.assertEqual(excess.status_code, 422, excess.text)
        bounced = self.client.patch(second_url, params=scope, json={'status': 'BOUNCED'})
        self.assertEqual(bounced.status_code, 200, bounced.text)
        self.assertEqual(bounced.json()['data']['status'], 'BOUNCED')
        fee = self.client.get(url, params=student_scope).json()['data'][0]
        self.assertEqual((fee['paid_amount'], fee['due_amount'], fee['status']), ('70.00', '30.00', 'PARTIAL'))
        after = self.client.get(adjustments_url, params=student_scope).json()['data']
        self.assertEqual([row['editable'] for row in after], [False, False])
        for adjustment_id, body in adjustment_ids:
            item_url = f'{adjustments_url}/{adjustment_id}'
            self.assertEqual(self.client.put(item_url, params=student_scope, json=dict(body, amount=9)).status_code, 409)
            self.assertEqual(self.client.delete(item_url, params=student_scope).status_code, 409)

    def test_bulk_one_time_array_saves_same_date_and_checks_combined_discount(self):
        student_id = self.make_student(1, 1, '001')
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal('100')
        self.db.commit()
        url = '/schools/student_fee_adjustments'
        scope = {'school_id': 1, 'session_id': 1}
        first = {'date': '2025-01-20', 'amount': 60, 'description': ''}
        second = {'date': '2025-01-20', 'amount': 41, 'description': 'Second discount'}
        payload = [{'admission_number': '001', 'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
                    'month': [first, second]}]

        openapi = self.client.get('/openapi.json').json()
        operation = openapi['paths'][f'{url}/bulk-upload/monthly']['post']
        example = operation['requestBody']['content']['application/json']['examples']['one_time_discount']['value']
        self.assertEqual(example, [
            {'admission_number': '004003', 'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity', 'month': [
                {'amount': 6291, 'date': '2026-11-20', 'description': ''},
                {'amount': 1791, 'date': '2026-11-20', 'description': ''},
            ]},
        ])

        extra = self.client.post(
            f'{url}/bulk-upload/monthly', params=scope, json=[dict(payload[0], **first)],
        )
        self.assertEqual(extra.status_code, 422, extra.text)
        self.assertEqual(
            {error['loc'][-1] for error in extra.json()['detail'] if error['type'] == 'extra_forbidden'},
            {'amount', 'date', 'description'},
        )

        rejected = self.client.post(f'{url}/bulk-upload/monthly', params=scope, json=payload)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(self.client.get(url, params=scope | {'student_id': student_id}).json()['total'], 0)

        second['amount'] = 40
        self.make_student(1, 3, '999')
        unknown = self.client.post(
            f'{url}/bulk-upload/monthly', params=scope, json=[*payload, dict(payload[0], admission_number='999')],
        )
        self.assertEqual(unknown.status_code, 404, unknown.text)
        self.assertEqual(unknown.json()['detail']['row'], 2)
        self.assertEqual(self.client.get(url, params=scope | {'student_id': student_id}).json()['total'], 0)

        saved = self.client.post(f'{url}/bulk-upload/monthly', params=scope, json=payload)
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertEqual(saved.json()['total'], 2)
        self.assertEqual(
            [(item['date'], Decimal(item['amount']), item['description'], item['tag'])
             for item in saved.json()['data'][0]['data']],
            [('2025-01-20', Decimal('60'), '', 'Bulk Upload'),
             ('2025-01-20', Decimal('40'), 'Second discount', 'Bulk Upload')],
        )

    def test_bulk_json_checks_monthly_discount_balances_and_saves_valid_entries(self):
        student_id = self.make_student(1, 1, '004003')
        other_id = self.make_student(1, 1, '3762')
        school_session = self.db.get(SchoolSession, 1)
        school_session.start_date = date(2024, 4, 10)
        school_session.end_date = date(2025, 2, 28)
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.month = '2024-04'
        carry.amount = Decimal('50')
        tuition = FeeHead(school_id=1, session_id=1, fee_head_name='Tuition', student_type='New')
        for fee_month in ('2024-05', '2025-01'):
            self.db.add(StudentFee(
                school_id=1, academic_session_id=1, student_id=student_id, fee_month=fee_month,
                total_amount=50, net_amount=50, paid_amount=0, status='UNPAID',
                fee_heads=[StudentFeeHead(fee_head=tuition, amount=50, net_amount=50, paid_amount=0)],
            ))
        self.db.commit()
        url = '/schools/student_fee_adjustments'
        scope = {'school_id': 1, 'session_id': 1}

        def upload(last_month, january_discount=30, second_discount_date='2024-05-01'):
            return self.client.post(f'{url}/bulk-upload/monthly', params=scope, json=[
                {'admission_number': '004003', 'type': 'Discount', 'frequency': 'Monthly', 'discount_type': 'Sibling',
                 'month': [{'date': '2024-04-01', 'amount': 10, 'description': 'Sibling discount'}, {'date': second_discount_date, 'amount': 20},
                           {'date': '2025-01-15', 'amount': january_discount, 'description': ''}]},
                {'admission_number': '3762', 'type': 'Penalty', 'frequency': 'Monthly',
                 'month': [{'date': '2024-04-01', 'amount': 18.50}, {'date': last_month, 'amount': 12}]},
                {'admission_number': '3762', 'type': 'Penalty', 'frequency': 'One Time',
                 'month': [{'amount': 25, 'date': '2024-11-20'}]},
            ])

        invalid = upload('2025-03-01')
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json()['detail']['row'], 2)
        self.assertEqual(self.client.get(url, params=scope | {'student_id': student_id}).json()['total'], 0)

        for response, fee_month, due, discount_amount in (
            (upload('2025-01-01', january_discount=70), '2025-01', '50.00', '70'),
            (upload('2025-01-01', second_discount_date='2024-06-01'), '2024-06', '0.00', '20'),
        ):
            self.assertEqual(response.status_code, 422, response.text)
            detail = response.json()['detail']
            self.assertEqual(detail['row'], 1)
            self.assertEqual(detail['fee_month'], fee_month)
            self.assertEqual(detail['due_amount'], due)
            self.assertEqual(detail['discount_amount'], discount_amount)
            self.assertEqual(self.db.query(StudentFeeAdjustment).count(), 0)

        response = upload('2025-01-01')
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['total'], 6)
        discount, penalty, one_time = response.json()['data']
        self.assertEqual(
            [row['admission_number'] for row in (discount, penalty, one_time)],
            ['004003', '3762', '3762'],
        )
        self.assertEqual(
            [item['admission_number'] for row in (discount, penalty, one_time) for item in row['data']],
            ['004003', '004003', '004003', '3762', '3762', '3762'],
        )
        self.assertEqual(
            [item.admission_number for item in self.db.query(StudentFeeAdjustment).order_by(StudentFeeAdjustment.id)],
            ['004003', '004003', '004003', '3762', '3762', '3762'],
        )
        self.assertEqual(discount['row'], 1)
        self.assertEqual(discount['skipped_months'], [])
        self.assertEqual([item['description'] for item in discount['data']], ['Sibling discount', '', ''])
        self.assertEqual(
            [(item['student_id'], item['date'], Decimal(item['amount']), item['tag'])
             for item in discount['data'] + penalty['data'] + one_time['data']],
            [(student_id, '2024-04-01', Decimal('10.00'), 'Bulk Upload'),
             (student_id, '2024-05-01', Decimal('20.00'), 'Bulk Upload'),
             (student_id, '2025-01-15', Decimal('30.00'), 'Bulk Upload'),
             (other_id, '2024-04-01', Decimal('18.50'), 'Bulk Upload'),
             (other_id, '2025-01-01', Decimal('12.00'), 'Bulk Upload'),
             (other_id, '2024-11-20', Decimal('25.00'), 'Bulk Upload')],
        )
        adjustment_id = discount['data'][-1]['id']
        # Simulate an oversized discount saved before bulk balance validation.
        self.db.get(StudentFeeAdjustment, adjustment_id).amount = Decimal('70')
        self.db.commit()
        adjustment_url = f"{url}/{adjustment_id}"
        student_scope = scope | {'student_id': student_id}
        revision = {'type': 'Discount', 'frequency': 'Monthly', 'discount_type': 'Sibling',
                    'date': '2025-01-15', 'amount': 51}
        rejected = self.client.put(adjustment_url, params=student_scope, json=revision)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()['detail']['discount_amount'], '51')
        updated = self.client.put(adjustment_url, params=student_scope, json=dict(revision, amount=45))
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(Decimal(updated.json()['data']['amount']), Decimal('45'))

    def test_student_list_filters_and_paginates_within_school_session(self):
        selected = self.db.get(Student, self.make_student(1, 1, '004003'))
        other = self.db.get(Student, self.make_student(1, 1, '3762'))
        unadjusted_id = self.make_student(1, 1, '9999')
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
        self.assertEqual(response.json()['total'], 0)
        self.assertEqual(response.json()['data'], [])
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

        for filters, expected in (
            ({'type': 'Discount', 'class_id': selected.class_id, 'section_id': section.id, 'search': 'aayat'},
             [selected.id]),
            ({'type': 'Penalty'}, [other.id]),
            ({'type': 'Penalty', 'section_id': section.id}, []),
            ({'type': 'All', 'class_id': 'all', 'section_id': 'ALL'}, [selected.id, other.id]),
            ({'type': 'all', 'section_id': section.id}, [selected.id]),
            ({'student_id': unadjusted_id}, []),
            ({'search': '9999'}, []),
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
        self.assertEqual(first['data'][0]['tag'], 'Bulk Upload')
        self.assertEqual(self.db.get(StudentFeeAdjustment, first['data'][0]['id']).admission_number, '001')
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

        self.assertFalse(first.json()['data']['editable'])
        revision = self.client.put(f'{url}/{adjustment_id}', params=scope, json=dict(payload, amount=65))
        self.assertEqual(revision.status_code, 409, revision.text)
        self.assertEqual(self.client.delete(f'{url}/{adjustment_id}', params=scope).status_code, 409)
        payment = self.client.get('/schools/fee_payments', params=dict(scope, fee_month='2025-01'))
        self.assertEqual(payment.status_code, 200, payment.text)
        self.assertEqual(payment.json()['data'][0]['due_amount'], '30.00')
        blocked = self.client.post(url, params=scope, json=dict(payload, amount=31, date='2025-01-20'))
        self.assertEqual(blocked.status_code, 422, blocked.text)
        self.assertEqual(blocked.json()['detail']['message'], 'Discount amount cannot exceed the remaining due')
        marked = self.client.post(f'{url}/{adjustment_id}/mark_applied', params=scope)
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual(marked.json()['message'], 'Discount applied successfully')
        self.assertEqual(marked.json()['data']['applied_at'], applied_at)

        self.db.delete(self.db.get(StudentFeeAdjustment, adjustment_id))
        self.db.commit()

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

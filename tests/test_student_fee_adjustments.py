import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, event, inspect, text

import catalog_setup
import test_sessions
import test_student_carry_forwards
from database.table_init import migrate_student_fee_adjustments
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

    def add_monthly_fee_head(self, student_id, month, amount):
        head = FeeHead(school_id=1, session_id=1, fee_head_name='Tuition', student_type='New')
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month=month,
            total_amount=amount, net_amount=amount, paid_amount=0, status='UNPAID',
            fee_heads=[StudentFeeHead(fee_head=head, amount=amount, net_amount=amount)],
        ))

    def test_migration_generates_database_payment_status_for_existing_and_new_rows(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE student_fee_adjustments (
                    id INTEGER PRIMARY KEY, type VARCHAR(20), applied_at DATETIME,
                    amount NUMERIC NOT NULL, paid_amount NUMERIC NOT NULL DEFAULT 0,
                    status VARCHAR(10) CHECK (status IN ('Pending', 'Closed'))
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO student_fee_adjustments (id, type, amount, paid_amount, status)
                VALUES (1, 'Discount', 100, 0, NULL), (2, 'Penalty', 100, 25, 'Closed'),
                       (3, 'Penalty', 100, 100, 'Closed'), (4, 'Discount', 100, 0, 'Pending')
            """)
            migrate_student_fee_adjustments(connection)
            connection.exec_driver_sql("ALTER TABLE student_fee_adjustments DROP COLUMN status")
            connection.exec_driver_sql("""
                ALTER TABLE student_fee_adjustments ADD COLUMN status VARCHAR(10) GENERATED ALWAYS AS (
                    CASE WHEN paid_amount >= amount THEN 'Paid' WHEN paid_amount > 0 THEN 'Partial' ELSE 'Pending' END
                ) VIRTUAL NOT NULL
            """)
            connection.exec_driver_sql("ALTER TABLE student_fee_adjustments ADD COLUMN application_status VARCHAR(10)")
            connection.exec_driver_sql("UPDATE student_fee_adjustments SET application_status = 'Pending' WHERE id = 4")
            migrate_student_fee_adjustments(connection)
            migrate_student_fee_adjustments(connection)
            self.assertNotIn("application_status", {
                column["name"] for column in inspect(connection).get_columns("student_fee_adjustments")
            })
            connection.exec_driver_sql("INSERT INTO student_fee_adjustments (id, amount) VALUES (5, 100)")
            self.assertEqual(connection.exec_driver_sql(
                "SELECT status, applied_at IS NOT NULL FROM student_fee_adjustments ORDER BY id"
            ).all(), [
                ("Paid", 1), ("Partial", 0), ("Paid", 0), ("Pending", 0), ("Pending", 0),
            ])
            connection.exec_driver_sql("UPDATE student_fee_adjustments SET paid_amount = 50 WHERE id = 5")
            self.assertEqual(connection.exec_driver_sql(
                "SELECT status FROM student_fee_adjustments WHERE id = 5"
            ).scalar_one(), "Partial")

    def test_late_payment_records_amounts_supplied_for_each_month(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        settings = FeeGenerationSettings(
            **scope, generation_day=1, payment_due_day=9, late_fee_enabled=True,
            late_fee_type='daily', late_fee_amount=10, min_due_amount=1,
        )
        self.db.add_all([tuition, settings])
        for month in range(1, 6):
            self.db.add(StudentFee(
                school_id=1, academic_session_id=1, student_id=student_id, fee_month=f'2025-{month:02d}',
                total_amount=100, net_amount=100, paid_amount=0, status='UNPAID',
                fee_heads=[StudentFeeHead(fee_head=tuition, amount=100, net_amount=100, paid_amount=0)],
            ))
        self.db.commit()
        student_scope = scope | {'student_id': student_id}
        payload = {
            'student_id': student_id, 'payment_mode': 'Cash',
            'months': [
                {'fee_month': '2025-02', 'payment_amount': 100, 'late_fee_payment_amount': '13.25'},
                {'fee_month': '2025-03', 'payment_amount': 100, 'late_fee_payment_amount': '481.90'},
            ],
        }
        url = '/schools/student_fees'
        ambiguous = self.client.post(url, params=scope, json=payload | {'late_fee_payment_amount': 100})
        self.assertEqual(ambiguous.status_code, 422, ambiguous.text)
        paid = self.client.post(url, params=scope, json=payload)
        self.assertEqual(paid.status_code, 201, paid.text)
        self.assertEqual(paid.json()['late_fee_paid_amount'], '495.15')
        self.assertEqual([row['late_fee_amount'] for row in paid.json()['data']], ['13.25', '481.90'])
        included = {
            'student_id': student_id, 'payment_mode': 'Cash', 'payment_amount': '107.50',
            'payment_amount_includes_late_fee': True, 'settle_in_full': True,
            'months': [{'fee_month': '2025-01', 'late_fee_payment_amount': '7.50'}],
        }
        short = self.client.post(url, params=scope, json=included | {'payment_amount': 100})
        self.assertEqual(short.status_code, 422, short.text)
        self.assertEqual(Decimal(short.json()['detail']['required_amount']), Decimal('107.50'))
        full = self.client.post(url, params=scope, json=included)
        self.assertEqual(full.status_code, 201, full.text)
        self.assertEqual(Decimal(full.json()['collected_amount']), Decimal('107.50'))
        self.assertEqual(Decimal(full.json()['fee_head_paid_amount']), 100)
        self.assertEqual(Decimal(full.json()['late_fee_paid_amount']), Decimal('7.50'))
        january = full.json()['balances'][0]
        self.assertEqual(Decimal(january['collected_amount']), Decimal('107.50'))
        self.assertEqual(Decimal(january['paid_amount']), Decimal('107.50'))
        self.assertEqual(Decimal(january['payable_amount']), Decimal('107.50'))
        self.assertEqual(january['status'], 'PAID')
        balances = self.client.get('/schools/fee_payments', params=student_scope).json()['data']
        self.assertEqual([row['status'] for row in balances[:5]], ['PAID', 'PAID', 'PAID', 'UNPAID', 'UNPAID'])

        response = self.client.post(url, params=scope, json=payload | {
            'months': [
                {'fee_month': '2025-05', 'payment_amount': 100},
                {'fee_month': '2025-04', 'payment_amount': 50},
            ],
            'late_fee_payment_amount': '51.00',
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['late_fee_paid_amount'], '51.00')
        self.assertEqual(
            {row['fee_month']: row['late_fee_amount'] for row in response.json()['data']},
            {'2025-04': '51.00', '2025-05': '0.00'},
        )
        april = next(row for row in response.json()['data'] if row['fee_month'] == '2025-04')
        self.assertEqual((april['status'], april['due_amount']), ('PARTIAL', '50.00'))

    def test_bulk_payments_use_imported_receipts_and_roll_unspent_cash_into_next_month(self):
        scope = {'school_id': 1, 'session_id': 1}
        students = {number: self.make_student(1, 1, number) for number in ('004001', '1')}
        self.make_student(1, 3, '009999')
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        starting_payments = {
            ('004001', '2025-04'): 100,
            ('004001', '2025-05'): 75,
            ('004001', '2025-06'): 0,
            ('004001', '2025-07'): 0,
            ('1', '2025-06'): 0,
        }
        for (admission_number, month), paid in starting_payments.items():
            self.db.add(StudentFee(
                school_id=1, academic_session_id=1, student_id=students[admission_number], fee_month=month,
                total_amount=100, net_amount=100, paid_amount=paid,
                status='PAID' if paid == 100 else 'PARTIAL' if paid else 'UNPAID',
                fee_heads=[StudentFeeHead(fee_head=tuition, amount=100, net_amount=100, paid_amount=paid)],
            ))
        self.db.commit()
        first_scope = scope | {'student_id': students['004001']}
        for adjustment in (
            {'type': 'Discount', 'discount_type': 'Charity', 'fee_head_id': tuition.id, 'amount': 20},
            {'type': 'Penalty', 'amount': 10},
        ):
            created = self.client.post('/schools/student_fee_adjustments', params=first_scope, json={
                **adjustment, 'frequency': 'One Time', 'date': '2025-06',
            })
            self.assertEqual(created.status_code, 201, created.text)

        url = '/schools/student_fees/bulk-upload'
        operation = self.client.get('/openapi.json').json()['paths'][url]['post']
        example = operation['requestBody']['content']['application/json']['examples']['payment_by_admission_number']
        self.assertEqual(example['value']['recieptNo'], '2026-2027/000002')
        first = {
            'admission_number': '004001', 'recieptNo': '2025-2026/000091', 'payment_mode': 'Cash',
            'fee_month': 'ignored date', 'payment_amount': 140, 'late_fee_payment_amount': 5,
            'cheque_number': 'string', 'tag': 'Bulk Upload',
        }
        second = {
            'admission_number': 1, 'recieptNo': '2025-2026/000042', 'payment_mode': 'Cash',
            'fee_month': '2099-01', 'payment_amount': 100,
        }
        for invalid, code in (
            (second | {'admission_number': '009999'}, 404),
            (second | {'payment_amount': 101}, 422),
        ):
            rejected = self.client.post(url, params=scope, json=[first, invalid])
            self.assertEqual(rejected.status_code, code, rejected.text)
            self.assertEqual(rejected.json()['detail']['row'], 2)
            self.assertEqual(rejected.json()['detail']['admission_number'], str(invalid['admission_number']))
            records = self.db.query(StudentFee).filter_by(student_id=students['004001']).all()
            self.assertTrue(all(
                record.paid_amount == starting_payments['004001', record.fee_month] and record.receipt_number is None
                for record in records
            ))

        saved = self.client.post(url, params=scope, json=[first, second])
        self.assertEqual(saved.status_code, 201, saved.text)
        response = saved.json()
        self.assertEqual(response['total'], 2)
        for row, supplied, amount in zip(response['data'], (first, second), (145, 100), strict=True):
            self.assertEqual(row['student_id'], students[str(supplied['admission_number'])])
            payment = row['result']
            self.assertEqual(payment['receipt_number'], supplied['recieptNo'])
            self.assertEqual(Decimal(payment['collected_amount']), amount)
            self.assertEqual({entry['tag'] for entry in payment['data']}, {'Bulk Upload'})
            self.assertEqual({entry['receipt_number'] for entry in payment['data']}, {supplied['recieptNo']})
        payment = response['data'][0]['result']
        self.assertEqual(Decimal(payment['penalty_paid_amount']), 10)
        self.assertEqual(Decimal(payment['late_fee_paid_amount']), 5)
        self.assertEqual(
            [(entry['fee_month'], Decimal(entry['paid_amount'])) for entry in payment['data']],
            [('2025-05', Decimal(105)), ('2025-06', Decimal(90)), ('2025-07', Decimal(25))],
        )
        self.assertEqual(Decimal(payment['data'][0]['late_fee_amount']), 5)
        previous = self.db.query(StudentFee).filter_by(student_id=students['004001'], fee_month='2025-04').one()
        self.assertEqual(previous.paid_amount, 100)
        self.assertIsNone(previous.receipt_number)
        single = self.client.post(url, params=scope, json={
            'admission_number': '004001', 'recieptNo': '2025-2026/000092', 'payment_amount': 75,
            'payment_mode': 'Cheque', 'cheque_number': '4321',
        })
        self.assertEqual(single.status_code, 201, single.text)
        cheque = single.json()['data'][0]['result']['data']
        self.assertEqual(cheque['receipt_number'], '2025-2026/000092')
        self.assertEqual(cheque['months'][0]['fee_month'], '2025-07')
        self.assertEqual(cheque['status'], 'PENDING')
        cleared = self.client.patch(f"/schools/student_fees/cheque_payments/{cheque['id']}",
                                    params=scope, json={'status': 'SUCCESS'})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()['data']['receipt_number'], '2025-2026/000092')
        july = self.db.query(StudentFee).filter_by(student_id=students['004001'], fee_month='2025-07').one()
        self.assertEqual(july.paid_amount, 100)
        self.assertEqual(july.receipt_number, '2025-2026/000092')
        self.assertEqual(self.db.execute(text("SELECT COUNT(*) FROM student_fee_receipt_counters")).scalar_one(), 0)

    def test_receipt_list_returns_only_student_fee_rows_with_optional_filters(self):
        scope = {'school_id': 1, 'session_id': 1}
        student_id = self.make_student(1, 1, '004001')
        legacy_student_id = self.make_student(1, 1, '004002')
        student = self.db.get(Student, student_id)
        student.first_name, student.middle_name, student.last_name = 'Aayat', 'Noor', 'Arbeea'
        section = Section(**scope, name='A')
        self.db.add(section)
        self.db.flush()
        student.section_id = section.id
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        for month in ('2025-05', '2025-06'):
            self.db.add(StudentFee(
                school_id=1, academic_session_id=1, student_id=student_id, fee_month=month,
                total_amount=100, net_amount=100, paid_amount=0, status='UNPAID',
                fee_heads=[StudentFeeHead(fee_head=tuition, amount=100, net_amount=100)],
            ))
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=legacy_student_id, fee_month='2025-05',
            total_amount=40, net_amount=40, paid_amount=40, status='PAID', receipt_number='legacy/10',
        ))
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=legacy_student_id, fee_month='2025-06',
            total_amount=30, net_amount=30, paid_amount=30, status='PAID', receipt_number='legacy/9',
        ))
        self.db.commit()

        imported_dates = (
            ('import/00001', 150, {'payment_date': '12-06-2025'}),
            ('import/00002', 25, {'fee_month': '13-06-2025', 'payment_time': '14:30:00'}),
        )
        for number, amount, supplied_timestamp in imported_dates:
            payment = self.client.post('/schools/student_fees/bulk-upload', params=scope, json={
                'admission_number': '004001', 'recieptNo': number, 'payment_amount': amount,
                'payment_mode': 'Cash', 'description': 'Imported installment', **supplied_timestamp,
            })
            self.assertEqual(payment.status_code, 201, payment.text)
            result = payment.json()['data'][0]['result']
            self.assertEqual({entry['payment_date'] for entry in result['data']}, {result['payment_date']})

        url = '/schools/student_fees/receipts'

        def receipts(**filters):
            response = self.client.get(url, params=scope | filters)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        receipt_queries = []

        def capture_receipt_query(_connection, _cursor, statement, _params, _context, _many):
            receipt_queries.append(statement.lower())

        event.listen(self.engine, 'before_cursor_execute', capture_receipt_query)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', capture_receipt_query)
        page = receipts(offset=1, limit=1)
        self.assertEqual(
            [row['receipt_number'] for row in receipts()['data']],
            ['import/00002', 'import/00001', 'legacy/10', 'legacy/9'],
        )
        self.assertEqual((page['total'], page['offset'], page['limit']), (4, 1, 1))
        self.assertEqual(page['data'][0]['receipt_number'], 'import/00001')
        self.assertEqual(datetime.fromisoformat(page['data'][0]['payment_date']).hour, 0)
        by_date = receipts(start_date='2025-06-13', end_date='2025-06-13')
        self.assertEqual([row['receipt_number'] for row in by_date['data']], ['import/00002'])
        self.assertEqual(datetime.fromisoformat(by_date['data'][0]['payment_date']).strftime('%H:%M'), '14:30')
        self.assertEqual(receipts(class_id=student.class_id, section_id=section.id)['total'], 2)
        self.assertEqual(receipts(search='004001')['total'], 2)
        self.assertEqual(receipts(search='aayat arbeea')['total'], 2)
        self.assertEqual(receipts(search='IMPORT/00002')['data'][0]['paid_amount'], '75.00')
        exact = receipts(search='₹100.00')['data'][0]
        self.assertEqual(exact['receipt_number'], 'import/00001')
        self.assertEqual(exact['description'], 'Imported installment')
        self.assertEqual(set(exact), set(StudentFee.__table__.columns.keys()))
        legacy = receipts(search='legacy')['data'][0]
        self.assertEqual(Decimal(legacy['paid_amount']), 40)
        self.assertIsNone(legacy['payment_date'])
        self.assertEqual(set(legacy), set(StudentFee.__table__.columns.keys()))
        self.assertTrue(receipt_queries)
        self.assertTrue(all('student_fee_heads' not in query and 'student_fee_receipts' not in query
                            for query in receipt_queries))
        invalid = self.client.get(url, params=scope | {'start_date': '2025-06-14', 'end_date': '2025-06-13'})
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_pay_now_amount_includes_supplied_late_fee_without_adding_it_again(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        student_scope = scope | {'student_id': student_id}
        self.db.get(SchoolSession, 1).name = '2026-2027'
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        composite = FeeHead(**scope, fee_head_name='Composite', student_type='New')
        months = ('2025-05', '2025-06')
        for month in months:
            self.db.add(StudentFee(
                school_id=1, academic_session_id=1, student_id=student_id, fee_month=month,
                total_amount=2600, net_amount=2600, status='UNPAID',
                fee_heads=[
                    StudentFeeHead(fee_head=tuition, amount=2500, net_amount=2500),
                    StudentFeeHead(fee_head=composite, amount=100, net_amount=100),
                ],
            ))
        self.db.commit()
        for month in months:
            for adjustment in (
                {'type': 'Discount', 'discount_type': 'Sibling', 'fee_head_id': tuition.id, 'amount': 200},
                {'type': 'Penalty', 'amount': 400},
            ):
                result = self.client.post('/schools/student_fee_adjustments', params=student_scope, json={
                    **adjustment, 'frequency': 'One Time', 'date': month,
                })
                self.assertEqual(result.status_code, 201, result.text)

        before_payment = datetime.now(timezone.utc)
        response = self.client.post('/schools/student_fees', params=scope, json={
            'student_id': student_id, 'payment_mode': 'Cash', 'payable_amount': 5643,
            'late_fee_payment_amount': 43, 'settle_in_full': True, 'tag': 'Bulk Upload',
            'months': [{'fee_month': month} for month in months],
        })
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        posted_at = datetime.fromisoformat(result['payment_date'])
        self.assertLessEqual(before_payment, posted_at)
        self.assertLessEqual(posted_at, datetime.now(timezone.utc))
        self.assertEqual({row['payment_date'] for row in result['data']}, {result['payment_date']})
        self.assertEqual(Decimal(result['collected_amount']), 5643)
        self.assertEqual(result['receipt_number'], '2026-2027/00001')
        self.assertEqual({row['receipt_number'] for row in result['data']}, {'2026-2027/00001'})
        self.assertEqual({row['tag'] for row in result['data']}, {'Bulk Upload'})
        self.assertEqual(Decimal(result['fee_head_paid_amount']), 4800)
        self.assertEqual(Decimal(result['penalty_paid_amount']), 800)
        self.assertEqual(Decimal(result['late_fee_paid_amount']), 43)
        self.assertEqual(sum(Decimal(row['paid_amount']) for row in result['data']), 5643)
        stored = self.db.execute(text(
            "SELECT SUM(paid_amount) FROM student_fees WHERE student_id = :student_id"
        ), {'student_id': student_id}).scalar_one()
        self.assertEqual(Decimal(stored), 5643)
        another = self.client.post('/schools/student_fees', params=scope, json={
            'student_id': student_id, 'payment_mode': 'Cash', 'payable_amount': 1,
            'late_fee_payment_amount': 1, 'months': [{'fee_month': months[0]}],
        })
        self.assertEqual(another.status_code, 201, another.text)
        self.assertEqual(another.json()['receipt_number'], '2026-2027/00002')
        self.assertIsNone(another.json()['data'][0]['tag'])
        self.assertTrue(all(row['status'] == 'PAID' and Decimal(row['total_due_amount']) == 0
                            for row in result['balances']))

    def test_total_payment_clears_earlier_month_and_only_partially_funds_last_head(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        transport = FeeHead(**scope, fee_head_name='Transport', student_type='New')
        activities = FeeHead(**scope, fee_head_name='Activities', student_type='New')
        self.db.add_all([tuition, transport, activities])
        self.db.flush()
        for month in ('2025-01', '2025-02', '2025-03'):
            self.db.add(StudentFee(
                school_id=1, academic_session_id=1, student_id=student_id, fee_month=month,
                total_amount=175, net_amount=175, paid_amount=0, status='UNPAID',
                fee_heads=[
                    StudentFeeHead(fee_head=tuition, amount=100, net_amount=100, paid_amount=0),
                    StudentFeeHead(fee_head=transport, amount=50, net_amount=50, paid_amount=0),
                    StudentFeeHead(fee_head=activities, amount=25, net_amount=25, paid_amount=0),
                ],
            ))
        self.db.commit()
        payload = {
            'student_id': student_id, 'payment_mode': 'Cash', 'payment_amount': '300.00',
            'months': [{'fee_month': month} for month in ('2025-03', '2025-02', '2025-01')],
        }
        url = '/schools/student_fees'
        self.assertEqual(self.client.post(url, params=scope, json=payload | {'payment_amount': '525.01'}).status_code, 422)
        response = self.client.post(url, params=scope, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            [(month['fee_month'], month['status'], month['due_amount']) for month in response.json()['data']],
            [('2025-01', 'PAID', '0.00'), ('2025-02', 'PARTIAL', '50.00')],
        )
        balances = self.client.get('/schools/fee_payments', params=scope | {'student_id': student_id}).json()['data']
        self.assertEqual(
            {head['fee_head_name']: (head['paid_amount'], head['due_amount']) for head in balances[1]['fee_heads']},
            {'Tuition': ('100.00', '0.00'), 'Transport': ('25.00', '25.00'), 'Activities': ('0.00', '25.00')},
        )
        self.assertEqual((balances[2]['status'], balances[2]['due_amount']), ('UNPAID', '175.00'))

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
        self.assertEqual(Decimal(result['data'][0]['fee_head_paid_amount']), 5600)
        self.assertEqual(Decimal(result['data'][0]['paid_amount']), 6900)
        self.assertEqual(Decimal(result['data'][0]['total_amount']), 6900)
        self.assertEqual(Decimal(result['data'][0]['total_accepted_payment']), 6900)
        self.assertEqual(Decimal(result['balances'][0]['total_accepted_payment']), 6900)
        balance = self.client.get('/schools/fee_payments', params=student_scope | {'fee_month': '2025-01'})
        month = balance.json()['data'][0]
        self.assertEqual((month['paid_amount'], month['due_amount'], month['status']), ('6900.00', '0.00', 'PAID'))
        self.assertEqual(month['fee_adjustment'][0]['due_amount'], '0.00')
        self.assertEqual(month['late_fee_paid_amount'], '1000.00')
        self.assertEqual(Decimal(month['total_accepted_payment']), 6900)
        self.assertEqual(Decimal(balance.json()['total_accepted_payment']), 6900)
        remainder = self.client.post(
            url, params=scope, json=payload | {
                'payment_mode': 'Cheque', 'cheque_number': '00120', 'late_fee_payment_amount': 120, 'tag': 'Bulk Upload',
                'months': [{'fee_month': '2025-01', 'payment_amount': 0}],
            },
        )
        self.assertEqual(remainder.status_code, 201, remainder.text)
        cheque = remainder.json()['data']
        self.assertEqual(cheque['status'], 'PENDING')
        self.assertIsNone(cheque['receipt_number'])
        self.assertEqual(cheque['tag'], 'Bulk Upload')
        self.assertEqual(
            self.client.get('/schools/fee_payments', params=student_scope).json()['data'][0]['late_fee_paid_amount'], '1000.00',
        )
        cleared = self.client.patch(
            f"{url}/cheque_payments/{cheque['id']}", params=scope, json={'status': 'SUCCESS'},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()['data']['receipt_number'], 'First/00002')
        settled = self.client.get('/schools/fee_payments', params=student_scope).json()['data'][0]
        self.assertEqual(settled['late_fee_paid_amount'], '1120.00')
        self.assertEqual(Decimal(settled['paid_amount']), 7020)
        self.assertEqual(Decimal(settled['total_accepted_payment']), 7020)
        record = self.db.query(StudentFee).filter_by(student_id=student_id, fee_month='2025-01').one()
        self.assertEqual(record.late_fee_amount, Decimal('1120.00'))
        self.assertEqual(record.tag, 'Bulk Upload')
        self.assertEqual(record.total_amount, Decimal('7020.00'))
        self.assertEqual(record.total_accepted_payment, Decimal('7020.00'))
        self.assertNotIn('total_accepted_payment', StudentFee.__table__.columns)
        repeated = self.client.post(url, params=scope, json=payload | {'late_fee_payment_amount': 0})
        self.assertEqual(repeated.status_code, 201, repeated.text)
        self.assertEqual(repeated.json()['data'], [])

    def test_cash_payment_skips_settled_carry_forward_and_months(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = carry.paid_amount = Decimal('50.00')
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        for month, paid in (('2025-02', 100), ('2025-03', 0)):
            self.db.add(StudentFee(
                school_id=1, academic_session_id=1, student_id=student_id, fee_month=month,
                total_amount=100, net_amount=100, paid_amount=paid, status='PAID' if paid else 'UNPAID',
                fee_heads=[StudentFeeHead(fee_head=tuition, amount=100, net_amount=100, paid_amount=paid)],
            ))
        self.db.commit()
        response = self.client.post('/schools/student_fees', params=scope, json={
            'student_id': student_id, 'payment_mode': 'Cash',
            'months': [
                {'fee_month': '2025-01', 'payment_amount': 50},
                {'fee_month': '2025-02', 'payment_amount': 100},
                {'fee_month': '2025-03', 'payment_amount': 25},
            ],
        })
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result['carry_forward_paid_amount'], '0.00')
        self.assertEqual(
            [(row['fee_month'], row['due_amount']) for row in result['data']], [('2025-03', '75.00')],
        )
        self.db.refresh(carry)
        self.assertEqual(carry.paid_amount, Decimal('50.00'))

    def test_late_fee_reuses_month_created_before_insert(self):
        student_id = self.make_student(1, 1, '001')
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = carry.paid_amount = Decimal('50.00')
        self.db.commit()
        inserted_ids = []

        def insert_month_first(conn, cursor, statement, parameters, context, executemany):
            if not inserted_ids and statement.startswith('INSERT INTO student_fees '):
                cursor.execute("""
                    INSERT INTO student_fees (
                        school_id, academic_session_id, student_id, fee_month,
                        total_amount, discount_amount, fine_amount, net_amount, paid_amount, status
                    ) VALUES (1, 1, ?, '2025-01', 0, 0, 0, 0, 0, 'PAID')
                """, (student_id,))
                inserted_ids.append(cursor.lastrowid)

        event.listen(self.engine, 'before_cursor_execute', insert_month_first)
        try:
            response = self.client.post('/schools/student_fees', params={'school_id': 1, 'session_id': 1}, json={
                'student_id': student_id, 'payment_mode': 'Cash',
                'months': [{'fee_month': '2025-01', 'payment_amount': 50}],
                'late_fee_payment_amount': '51.00',
            })
        finally:
            event.remove(self.engine, 'before_cursor_execute', insert_month_first)
        self.assertEqual(response.status_code, 201, response.text)
        record = self.db.query(StudentFee).filter_by(student_id=student_id, fee_month='2025-01').one()
        self.assertEqual((record.id, record.late_fee_amount), (inserted_ids[0], Decimal('51.00')))

    def test_late_fee_payment_works_when_legacy_column_has_no_default(self):
        student_id = self.make_student(1, 1, '001')
        with self.engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE student_fees DROP COLUMN late_fee_amount')
            connection.exec_driver_sql('ALTER TABLE student_fees ADD COLUMN late_fee_amount NUMERIC(16, 2) NOT NULL')

        response = self.client.post('/schools/student_fees', params={'school_id': 1, 'session_id': 1}, json={
            'student_id': student_id, 'payment_mode': 'Cash',
            'months': [{'fee_month': '2025-01', 'payment_amount': 0}],
            'late_fee_payment_amount': '51.00',
        })
        self.assertEqual(response.status_code, 201, response.text)
        record = self.db.query(StudentFee).filter_by(student_id=student_id, fee_month='2025-01').one()
        self.assertEqual(record.late_fee_amount, Decimal('51.00'))

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
        self.add_monthly_fee_head(student_id, '2025-01', 100)
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

        first_id = saved.json()['data'][0]['data'][0]['id']
        self.db.get(StudentFeeAdjustment, first_id).amount = Decimal('200')
        self.db.commit()
        balance = self.client.get('/schools/fee_payments', params=scope | {
            'student_id': student_id, 'fee_month': '2025-01',
        }).json()['data'][0]
        self.assertEqual(Decimal(balance['carry_forward']['due_amount']), 100)
        self.assertEqual(Decimal(balance['due_amount']), 200)
        preview = self.client.get('/schools/fee_payments', params=scope | {
            'student_id': student_id, 'fee_month': '2025-01', 'include_pending_discounts': True,
        }).json()['data'][0]
        self.assertEqual(Decimal(preview['due_amount']), 100)

        payment = self.client.post('/schools/student_fees', params=scope, json={
            'student_id': student_id, 'payment_mode': 'Cash',
            'months': [{'fee_month': '2025-01', 'payment_amount': 100}],
        })
        self.assertEqual(payment.status_code, 201, payment.text)
        self.assertEqual(Decimal(payment.json()['carry_forward_paid_amount']), 100)
        self.assertEqual(Decimal(payment.json()['data'][0]['total_accepted_payment']), 100)

    def test_single_and_bulk_adjustments_update_only_the_selected_fee_head(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        student_scope = scope | {'student_id': student_id}
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        transport = FeeHead(**scope, fee_head_name='Transport', student_type='New')
        unassigned = FeeHead(**scope, fee_head_name='Library', student_type='New')
        self.db.add(unassigned)
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-02',
            total_amount=200, net_amount=200, paid_amount=0, status='UNPAID',
            fee_heads=[StudentFeeHead(fee_head=head, amount=100, net_amount=100) for head in (tuition, transport)],
        ))
        self.db.commit()
        url = '/schools/student_fee_adjustments'
        row = {
            'admission_number': '001', 'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
            'fee_head_id': tuition.id, 'status': 'pending', 'month': [{'date': '2025-02', 'amount': 101}],
        }

        rejected = self.client.post(f'{url}/bulk-upload/monthly', params=scope, json=[
            row | {'fee_head_id': unassigned.id},
        ])
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()['detail']['message'], 'Fee head is not assigned for this month')

        saved = self.client.post(f'{url}/bulk-upload/monthly', params=scope, json=[row])
        self.assertEqual(saved.status_code, 201, saved.text)
        adjustment = saved.json()['data'][0]['data'][0]
        self.assertEqual((adjustment['fee_head_id'], adjustment['status']), (tuition.id, 'Pending'))
        self.assertIsNone(adjustment['applied_at'])
        pending = self.client.get(url, params=student_scope | {'status': 'Pending'})
        self.assertEqual(pending.json()['total'], 1)

        def february_balance(*, preview=False):
            response = self.client.get('/schools/fee_payments', params=student_scope | {
                'fee_month': '2025-02', 'include_pending_discounts': preview,
            })
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['data'][0]

        self.assertEqual(Decimal(february_balance()['due_amount']), 200)
        self.assertEqual(Decimal(february_balance(preview=True)['due_amount']), 100)
        applied = self.client.post(f"{url}/{adjustment['id']}/mark_applied", params=student_scope)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()['data']['status'], 'Paid')
        self.assertIsNotNone(applied.json()['data']['applied_at'])
        self.assertEqual(Decimal(applied.json()['data']['amount']), 101)
        balance = february_balance()
        self.assertEqual({head['fee_head_id']: Decimal(head['due_amount']) for head in balance['fee_heads']}, {
            tuition.id: Decimal(0), transport.id: Decimal(100),
        })
        self.assertEqual(balance['fee_adjustment'][0]['fee_head_id'], tuition.id)
        single_payload = {
            'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
            'fee_head_id': transport.id, 'date': '2025-02', 'amount': 10,
        }
        invalid_single = self.client.post(
            url, params=student_scope, json=single_payload | {'fee_head_id': unassigned.id},
        )
        self.assertEqual(invalid_single.status_code, 422, invalid_single.text)
        single = self.client.post(url, params=student_scope, json=single_payload)
        self.assertEqual(single.status_code, 201, single.text)
        single_discount = single.json()['data']
        self.assertEqual(single_discount['fee_head_id'], transport.id)
        self.assertEqual(single_discount['admission_number'], '001')
        self.assertEqual(single_discount['status'], 'Pending')
        self.assertEqual(Decimal(single_discount['paid_amount']), 0)
        self.assertIsNone(single_discount['applied_at'])

        penalty_row = {
            'admission_number': '001', 'type': 'Penalty', 'frequency': 'One Time',
            'fee_head_id': tuition.id, 'month': [{'date': '2025-02', 'amount': 25}],
        }
        rejected = self.client.post(f'{url}/bulk-upload/monthly', params=scope, json=[
            penalty_row | {'fee_head_id': unassigned.id},
        ])
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()['detail']['fee_head_id'], unassigned.id)
        penalty = self.client.post(f'{url}/bulk-upload/monthly', params=scope, json=[penalty_row])
        self.assertEqual(penalty.status_code, 201, penalty.text)
        self.assertEqual(penalty.json()['data'][0]['data'][0]['fee_head_id'], tuition.id)
        self.assertEqual(penalty.json()['data'][0]['status'], 'Pending')
        self.assertEqual(penalty.json()['data'][0]['data'][0]['status'], 'Pending')
        flat_upload = self.client.post(f'{url}/bulk-upload', params=student_scope, json=[
            {'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
             'fee_head_id': transport.id, 'date': '2025-02', 'amount': 5, 'status': None},
            {'type': 'Penalty', 'frequency': 'One Time',
             'fee_head_id': tuition.id, 'date': '2025-02', 'amount': 5, 'status': ' '},
        ])
        self.assertEqual(flat_upload.status_code, 201, flat_upload.text)
        self.assertEqual([entry['status'] for entry in flat_upload.json()['data']], ['Pending', 'Pending'])
        self.assertEqual(
            [(entry['data'][0]['fee_head_id'], entry['data'][0]['status']) for entry in flat_upload.json()['data']],
            [(transport.id, 'Pending'), (tuition.id, 'Pending')],
        )
        flat_discount_id = flat_upload.json()['data'][0]['data'][0]['id']
        saved_statuses = self.db.execute(text(
            "SELECT status FROM student_fee_adjustments WHERE id IN (:monthly_id, :flat_id, :single_id) ORDER BY id"
        ), {'monthly_id': adjustment['id'], 'flat_id': flat_discount_id, 'single_id': single_discount['id']}).scalars().all()
        self.assertEqual(saved_statuses, ['Paid', 'Pending', 'Pending'])
        for endpoint, filters in ((url, {}), (f'{url}/students/adjustments', {'type': 'Penalty'})):
            filtered = self.client.get(endpoint, params=student_scope | filters | {'fee_head_id': tuition.id})
            self.assertEqual(filtered.status_code, 200, filtered.text)
            self.assertEqual(filtered.json()['total'], 2 if filters else 3)
            self.assertTrue(all(item['fee_head_id'] == tuition.id for item in filtered.json()['data']))
        self.assertEqual(Decimal(february_balance()['due_amount']), 130)
        balance = february_balance(preview=True)
        self.assertEqual(Decimal(balance['due_amount']), 115)
        self.assertEqual({head['fee_head_id']: Decimal(head['due_amount']) for head in balance['fee_heads']}, {
            tuition.id: Decimal(30), transport.id: Decimal(85),
        })

        def pay(amount):
            return self.client.post('/schools/student_fees', params=scope, json={
                'student_id': student_id, 'payment_mode': 'Cash',
                'months': [{'fee_month': '2025-02', 'payment_amount': amount}],
            })

        partial_payment = pay(95)
        self.assertEqual(partial_payment.status_code, 201, partial_payment.text)
        self.assertEqual(Decimal(partial_payment.json()['penalty_paid_amount']), 10)
        payment_record = partial_payment.json()['data'][0]
        self.assertEqual(Decimal(payment_record['discount_amount']), 115)
        self.assertEqual(Decimal(payment_record['net_amount']), 85)
        self.assertEqual(payment_record['status'], 'PAID')
        self.assertEqual(
            {head['fee_head_id']: Decimal(head['discount_amount']) for head in payment_record['fee_heads']},
            {tuition.id: Decimal(100), transport.id: Decimal(15)},
        )
        self.assertEqual(Decimal(february_balance()['due_amount']), 20)
        partial = self.client.get(url, params=student_scope | {'status': 'Partial'})
        self.assertEqual(partial.status_code, 200, partial.text)
        self.assertEqual(partial.json()['total'], 1)
        self.assertEqual(partial.json()['data'][0]['status'], 'Partial')
        self.assertEqual(Decimal(partial.json()['data'][0]['paid_amount']), 10)

        paid = pay(20)
        self.assertEqual(paid.status_code, 201, paid.text)
        self.assertEqual(Decimal(paid.json()['penalty_paid_amount']), 20)
        balance = february_balance()
        self.assertEqual(balance['status'], 'PAID')
        paid_adjustments = self.client.get(url, params=student_scope | {'status': 'Paid'})
        self.assertEqual(paid_adjustments.status_code, 200, paid_adjustments.text)
        self.assertEqual(sorted(Decimal(item['paid_amount']) for item in paid_adjustments.json()['data']), [0, 0, 0, 5, 25])
        self.assertTrue(all(item['status'] == 'Paid' for item in paid_adjustments.json()['data']))
        self.assertEqual({head['fee_head_id']: Decimal(head['paid_amount']) for head in balance['fee_heads']}, {
            tuition.id: Decimal(30), transport.id: Decimal(85),
        })

    def test_assigned_head_discount_leaves_other_head_and_carry_forward_payable(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        student_scope = scope | {'student_id': student_id}
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal(50)
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        transport = FeeHead(**scope, fee_head_name='Transport', student_type='New')
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-01',
            total_amount=200, net_amount=200, paid_amount=0, status='UNPAID',
            fee_heads=[StudentFeeHead(fee_head=head, amount=100, net_amount=100) for head in (tuition, transport)],
        ))
        self.db.commit()

        upload = self.client.post('/schools/student_fee_adjustments/bulk-upload', params=student_scope, json=[{
            'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
            'fee_head_id': tuition.id, 'date': '2025-01', 'amount': 175,
        }])
        self.assertEqual(upload.status_code, 201, upload.text)
        balance = self.client.get('/schools/fee_payments', params=student_scope | {
            'fee_month': '2025-01', 'include_pending_discounts': True,
        })
        self.assertEqual(balance.status_code, 200, balance.text)
        month = balance.json()['data'][0]
        self.assertEqual(Decimal(month['carry_forward']['due_amount']), 50)
        self.assertEqual({head['fee_head_id']: Decimal(head['due_amount']) for head in month['fee_heads']}, {
            tuition.id: Decimal(0), transport.id: Decimal(100),
        })
        self.assertEqual(Decimal(month['due_amount']), 150)

    def test_payment_response_keeps_partial_penalty_due_after_discounted_fees_are_paid(self):
        self.check_discounted_april_payment([(5803, 100, 'PARTIAL', 100), (100, 100, 'PAID', 0)])

    def test_single_net_payment_pays_all_discounted_fees_carry_forward_and_penalty(self):
        self.check_discounted_april_payment([(5903, 200, 'PAID', 0)], settle_in_full=True)

    def check_discounted_april_payment(self, payments, *, settle_in_full=False):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        student_scope = scope | {'student_id': student_id}
        self.db.get(SchoolSession, 1).start_date = date(2025, 4, 1)
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.month, carry.amount = '2025-04', Decimal(203)
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-04',
            total_amount=5600, net_amount=5600, paid_amount=0, status='UNPAID',
            fee_heads=[StudentFeeHead(fee_head=tuition, amount=2500, net_amount=2500)] + [
                StudentFeeHead(fee_head=FeeHead(**scope, fee_head_name=name, student_type='New'),
                               amount=amount, net_amount=amount)
                for name, amount in (('Admission', 3000), ('Composite', 100))
            ],
        ))
        self.db.commit()
        for adjustment in (
            {'type': 'Discount', 'fee_head_id': tuition.id, 'discount_type': 'Charity', 'amount': 100},
            {'type': 'Penalty', 'amount': 200},
        ):
            result = self.client.post('/schools/student_fee_adjustments', params=student_scope, json={
                **adjustment, 'frequency': 'One Time', 'date': '2025-04',
            })
            self.assertEqual(result.status_code, 201, result.text)

        payment_url = '/schools/student_fees'
        payment = {
            'student_id': student_id, 'payment_mode': 'Cash', 'settle_in_full': settle_in_full,
            'months': [{'fee_month': '2025-04'}],
        }
        if settle_in_full:
            for short in (
                payment | {'payment_amount': 5803},
                payment | {'months': [{'fee_month': '2025-04', 'payment_amount': 5803}]},
            ):
                response = self.client.post(payment_url, params=scope, json=short)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(Decimal(response.json()['detail']['required_amount']), 5903)

        def pay(amount):
            if settle_in_full:
                supplied = payment | {'payable_amount': amount}
            else:
                supplied = payment | {'months': [{'fee_month': '2025-04', 'payable_amount': amount}]}
            response = self.client.post(payment_url, params=scope, json=supplied)
            self.assertEqual(response.status_code, 201, response.text)
            return response.json()

        for amount, penalty_paid, status, due in payments:
            result = pay(amount)
            self.assertEqual(Decimal(result['penalty_paid_amount']), penalty_paid)
            self.assertEqual(Decimal(result['collected_amount']), amount)
            self.assertEqual(Decimal(result['data'][0]['total_amount']), 6003)
            self.assertEqual(Decimal(result['data'][0]['discount_amount']), 100)
            self.assertEqual(Decimal(result['data'][0]['paid_amount']), 5903 - due)
            self.assertEqual(Decimal(result['data'][0]['total_accepted_payment']), 5903 - due)
            balance = result['balances'][0]
            self.assertEqual(balance['status'], status)
            self.assertEqual(Decimal(balance['payable_amount']), 5903)
            self.assertEqual(Decimal(balance['total_due_amount']), due)
            discount, penalty = balance['fee_adjustment']
            self.assertEqual(Decimal(discount['amount']), 100)
            self.assertEqual(Decimal(discount['discount_amount']), 100)
            self.assertEqual(Decimal(balance['total_discount_amount']), 100)
            self.assertEqual(Decimal(discount['payable_amount']), 100)
            self.assertEqual(Decimal(discount['due_amount']), 0)
            self.assertEqual(Decimal(penalty['amount']), 200)
            self.assertEqual(Decimal(penalty['penalty_amount']), 200)
            self.assertEqual(Decimal(penalty['penalty_paid_amount']), 200 - due)
            self.assertIsNone(penalty['fee_head_payable_amount'])
            self.assertEqual(Decimal(penalty['due_amount']), due)
            self.assertEqual(Decimal(balance['total_penalty_amount']), 200)
            self.assertEqual(Decimal(balance['penalty_paid_amount']), 200 - due)
            receipt = self.client.get('/schools/student_fees/receipts', params=scope | {
                'search': result['receipt_number'],
            })
            self.assertEqual(receipt.status_code, 200, receipt.text)
            receipt_row = receipt.json()['data'][0]
            self.assertEqual(receipt_row['status'], 'PAID')
            self.assertEqual(Decimal(receipt_row['paid_amount']), 5903 - due)
            self.assertEqual(Decimal(balance['penalty_due_amount']), due)
            self.assertEqual(penalty['status'], 'Partial' if due else 'Paid')
            self.assertEqual(discount['status'], 'Paid')
            lines = balance['fee_heads'] + [penalty, balance['carry_forward']]
            self.assertEqual(sum(Decimal(line['payable_amount']) for line in lines), 5903)
            self.assertEqual(sum(Decimal(line['due_amount']) for line in lines), due)
            refreshed = self.client.get('/schools/fee_payments', params=student_scope | {'fee_month': '2025-04'})
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            self.assertEqual(refreshed.json()['data'][0], balance)

    def test_collecting_net_cash_marks_discounted_tuition_and_month_paid(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        student_scope = scope | {'student_id': student_id}
        tuition = FeeHead(**scope, fee_head_name='Tuition Fee', student_type='New')
        composite = FeeHead(**scope, fee_head_name='Composite Fee', student_type='New')
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-09',
            total_amount=2100, net_amount=2100, paid_amount=0, status='UNPAID',
            fee_heads=[
                StudentFeeHead(fee_head=tuition, amount=2000, net_amount=2000),
                StudentFeeHead(fee_head=composite, amount=100, net_amount=100),
            ],
        ))
        self.db.commit()
        discount = self.client.post('/schools/student_fee_adjustments', params=student_scope, json={
            'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
            'fee_head_id': tuition.id, 'date': '2025-09', 'amount': 500,
        })
        self.assertEqual(discount.status_code, 201, discount.text)
        self.assertEqual(discount.json()['data']['status'], 'Pending')

        def balance(*, preview=False):
            response = self.client.get('/schools/fee_payments', params=student_scope | {
                'fee_month': '2025-09', 'include_pending_discounts': preview,
            })
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result['total_payable_amount'], result['data'][0]['payable_amount'])
            return result['data'][0]

        all_months = self.client.get('/schools/fee_payments', params=student_scope).json()
        self.assertGreater(all_months['total'], 1)
        self.assertEqual(
            Decimal(all_months['total_payable_amount']),
            sum(Decimal(month['payable_amount']) for month in all_months['data']),
        )
        posted = balance()
        self.assertEqual(Decimal(posted['due_amount']), 2100)
        self.assertEqual(Decimal(posted['total_discount_amount']), 0)
        self.assertEqual(Decimal(posted['fee_adjustment'][0]['amount']), 500)
        self.assertEqual(posted['fee_adjustment'][0]['status'], 'Pending')
        self.assertEqual(Decimal(posted['fee_adjustment'][0]['fee_head_payable_amount']), 2000)
        self.assertEqual(Decimal(posted['fee_adjustment'][0]['payable_amount']), 500)
        self.assertEqual(Decimal(posted['fee_adjustment'][0]['due_amount']), 500)
        quote = balance(preview=True)
        self.assertEqual(Decimal(quote['due_amount']), 1600)
        quoted_heads = {head['fee_head_id']: head for head in quote['fee_heads']}
        self.assertEqual(Decimal(quoted_heads[tuition.id]['original_amount']), 2000)
        self.assertEqual(Decimal(quoted_heads[tuition.id]['payable_amount']), 1500)
        self.assertEqual(quote['fee_adjustment'][0]['status'], 'Pending')
        self.assertEqual(Decimal(quote['fee_adjustment'][0]['fee_head_payable_amount']), 1500)
        self.assertEqual(Decimal(quote['fee_adjustment'][0]['payable_amount']), 500)
        self.assertEqual(Decimal(quote['fee_adjustment'][0]['due_amount']), 500)
        payment_url = '/schools/student_fees'
        payload = {'student_id': student_id, 'payment_mode': 'Cash', 'months': [{'fee_month': '2025-09'}]}
        excess = self.client.post(payment_url, params=scope, json=payload | {'payment_amount': 2100})
        self.assertEqual(excess.status_code, 422, excess.text)
        payment = self.client.post(payment_url, params=scope, json=payload | {'payment_amount': 1600})
        self.assertEqual(payment.status_code, 201, payment.text)
        self.assertEqual(Decimal(payment.json()['data'][0]['total_amount']), 2100)

        for month in (payment.json()['data'][0], balance()):
            self.assertEqual(month['status'], 'PAID')
            self.assertEqual(Decimal(month['due_amount']), 0)
            self.assertEqual(Decimal(month['paid_amount']), 1600)
            by_id = {head['fee_head_id']: head for head in month['fee_heads']}
            for fee_head, payable, applied_discount in ((tuition, 1500, 500), (composite, 100, 0)):
                head = by_id[fee_head.id]
                self.assertEqual(head['status'], 'PAID')
                self.assertEqual(Decimal(head['payable_amount']), payable)
                self.assertEqual(Decimal(head['paid_amount']), payable)
                self.assertEqual(Decimal(head['discount_amount']), applied_discount)
                self.assertEqual(Decimal(head['due_amount']), 0)
        posted_heads = {head['fee_head_id']: head for head in balance()['fee_heads']}
        self.assertEqual(Decimal(posted_heads[tuition.id]['original_amount']), 2000)
        self.assertEqual(Decimal(posted_heads[tuition.id]['payable_amount']), 1500)
        adjustment = balance()['fee_adjustment'][0]
        self.assertEqual(Decimal(adjustment['discount_amount']), 500)
        self.assertEqual(Decimal(adjustment['fee_head_paid_amount']), 1500)
        self.assertEqual(Decimal(adjustment['fee_head_due_amount']), 0)
        self.assertEqual(adjustment['fee_head_status'], 'PAID')
        self.assertEqual(adjustment['status'], 'Paid')
        self.assertEqual(Decimal(adjustment['payable_amount']), 500)
        self.assertEqual(Decimal(adjustment['paid_amount']), 0)
        self.assertEqual(Decimal(adjustment['due_amount']), 0)

    def test_payment_records_fully_discounted_head_without_principal_payment(self):
        student_id = self.make_student(1, 1, '001')
        scope = {'school_id': 1, 'session_id': 1}
        tuition = FeeHead(**scope, fee_head_name='Tuition', student_type='New')
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-02',
            total_amount=100, net_amount=100, paid_amount=0, status='UNPAID',
            fee_heads=[StudentFeeHead(fee_head=tuition, amount=100, net_amount=100)],
        ))
        self.db.commit()
        response = self.client.post('/schools/student_fee_adjustments', params=scope | {'student_id': student_id}, json={
            'type': 'Discount', 'frequency': 'One Time', 'discount_type': 'Charity',
            'fee_head_id': tuition.id, 'date': '2025-02', 'amount': 100,
        })
        self.assertEqual(response.status_code, 201, response.text)

        for _ in range(2):
            payment = self.client.post('/schools/student_fees', params=scope, json={
                'student_id': student_id, 'payment_mode': 'Cash',
                'months': [{'fee_month': '2025-02', 'payment_amount': 0}],
            })
            self.assertEqual(payment.status_code, 201, payment.text)
            fee = payment.json()['data'][0]
            self.assertEqual(fee['status'], 'PAID')
            self.assertEqual(Decimal(fee['discount_amount']), 100)
            self.assertEqual(Decimal(fee['paid_amount']), 0)
            self.assertEqual(Decimal(fee['net_amount']), 0)
            self.assertEqual(Decimal(fee['fee_heads'][0]['discount_amount']), 100)

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
        for fee_month in ('2024-04', '2024-05', '2025-01'):
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
        self.add_monthly_fee_head(selected.id, '2025-01', 100)
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
        self.add_monthly_fee_head(student_id, '2025-01', 60)
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
        self.assertIsNone(third['data'][0]['applied_at'])
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

    def test_discount_excludes_carry_forward_and_respects_existing_discounts(self):
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

        too_high = self.client.post(url, params=scope, json=dict(payload, amount='50.01'))
        self.assertEqual(too_high.status_code, 422, too_high.text)
        self.assertEqual(too_high.json()['detail']['due_amount'], '50.00')
        monthly = self.client.post(url, params=scope, json=dict(payload, amount=71, frequency='Monthly'))
        self.assertEqual(monthly.status_code, 422, monthly.text)
        self.assertEqual(monthly.json()['detail']['skipped_months'][0]['fee_month'], '2025-01')
        self.assertEqual(self.client.get(url, params=scope).json()['total'], 0)

        first = self.client.post(url, params=scope, json=dict(payload, amount=40))
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()['message'], 'Discount assigned successfully')
        self.assertEqual(first.json()['data']['date'], '2025-01-01')
        self.assertEqual(first.json()['data']['description'], '')
        self.assertIsNone(first.json()['data']['applied_at'])
        adjustment_id = first.json()['data']['id']
        rejected = self.client.post(url, params=scope, json=dict(payload, amount=11))
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()['detail']['due_amount'], '10.00')

        self.assertFalse(first.json()['data']['editable'])
        revision = self.client.put(f'{url}/{adjustment_id}', params=scope, json=dict(payload, amount=65))
        self.assertEqual(revision.status_code, 409, revision.text)
        self.assertEqual(self.client.delete(f'{url}/{adjustment_id}', params=scope).status_code, 409)
        payment = self.client.get('/schools/fee_payments', params=dict(scope, fee_month='2025-01'))
        self.assertEqual(payment.status_code, 200, payment.text)
        self.assertEqual(payment.json()['data'][0]['due_amount'], '70.00')
        self.assertEqual(payment.json()['data'][0]['carry_forward']['due_amount'], '20.00')
        blocked = self.client.post(url, params=scope, json=dict(payload, amount=31, date='2025-01-20'))
        self.assertEqual(blocked.status_code, 422, blocked.text)
        self.assertEqual(blocked.json()['detail']['message'], 'Discount amount cannot exceed the remaining due')
        marked = self.client.post(f'{url}/{adjustment_id}/mark_applied', params=scope)
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual(marked.json()['message'], 'Discount applied successfully')
        self.assertIsNotNone(marked.json()['data']['applied_at'])

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
        self.assertEqual(skipped['2025-02']['due_amount'], '0.00')
        self.assertEqual(skipped['2025-03']['due_amount'], '0.00')
        self.assertEqual(skipped['2025-04']['due_amount'], '0.00')
        adjustments = self.client.get(url, params=scope).json()['data']
        self.assertEqual(
            [row['date'] for row in adjustments if row['type'] == 'Discount'],
            ['2025-01-01'],
        )

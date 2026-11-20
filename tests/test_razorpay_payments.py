import hmac
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import catalog_setup
import test_sessions
import test_student_carry_forwards
from models.fee_head import FeeHead
from models.generation_settings import FeeGenerationSettings
from models.student_carry_forward import StudentCarryForward
from models.student_fee import StudentFee, StudentFeeHead
from routers.razorpay_payments import RazorpayClient


class RazorpayPaymentTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown
    make_student = test_student_carry_forwards.StudentCarryForwardBulkTests.make_student

    def test_only_a_verified_capture_posts_the_stored_allocation_once(self):
        student_id = self.make_student(1, 1, '001')
        carry = self.db.query(StudentCarryForward).filter_by(student_id=student_id).one()
        carry.amount = Decimal('20')
        self.db.add(FeeGenerationSettings(
            school_id=1, session_id=1, generation_day=1, payment_due_day=9,
            late_fee_enabled=True, late_fee_type='monthly', late_fee_amount=Decimal('5.50'), min_due_amount=1,
        ))
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=student_id, fee_month='2025-01',
            total_amount=100, net_amount=100, paid_amount=0, status='UNPAID',
            fee_heads=[StudentFeeHead(
                fee_head=FeeHead(school_id=1, session_id=1, fee_head_name='Tuition', student_type='New'),
                amount=100, net_amount=100, paid_amount=0,
            )],
        ))
        self.db.commit()

        base = '/schools/student_fees'
        url = f'{base}/razorpay'
        scope = {'school_id': 1, 'session_id': 1}
        payload = {'student_id': student_id, 'months': [{'fee_month': '2025-01', 'payment_amount': '120.00'}],
                   'late_fee_payment_amount': '5.50'}
        payment = {'id': 'pay_example', 'order_id': 'order_example', 'currency': 'INR', 'amount': 12550}
        signature = hmac.new(b'checkout-secret', b'order_example|pay_example', 'sha256').hexdigest()
        callback = {'razorpay_order_id': 'order_example', 'razorpay_payment_id': 'pay_example',
                    'razorpay_signature': signature}
        keys = {'RAZORPAY_KEY_ID': 'rzp_test_example', 'RAZORPAY_KEY_SECRET': 'checkout-secret',
                'RAZORPAY_WEBHOOK_SECRET': 'webhook-secret'}

        with patch.dict('os.environ', keys), patch.object(RazorpayClient, 'request') as gateway:
            gateway.return_value = {'id': 'order_example', 'currency': 'INR', 'amount': 12550}
            order = self.client.post(f'{url}/orders', params=scope, json=payload)
            self.assertEqual(order.status_code, 201, order.text)
            self.assertEqual(order.json(), {'status': 'success', 'data': {
                'order_id': 'order_example', 'amount': 12550, 'currency': 'INR', 'key_id': 'rzp_test_example',
            }})
            self.assertEqual(gateway.call_args.args[2]['amount'], 12550)

            rejected = self.client.post(f'{url}/verify', params=scope, json=callback | {'razorpay_signature': '0' * 64})
            self.assertEqual(rejected.status_code, 400, rejected.text)
            gateway.return_value = payment | {'status': 'authorized'}
            pending = self.client.post(f'{url}/verify', params=scope, json=callback)
            self.assertEqual(pending.status_code, 409, pending.text)
            unpaid = self.client.get(base, params=scope | {'student_id': student_id}).json()['data'][0]
            self.assertEqual(unpaid['paid_amount'], '0.00')
            self.assertEqual(carry.paid_amount, Decimal('0'))

            for amount, expected_status in ((12560, 409), (12550, 204), (12550, 204)):
                body = json.dumps({
                    'event': 'payment.captured',
                    'payload': {'payment': {'entity': payment | {'amount': amount, 'status': 'captured'}}},
                }).encode()
                webhook_signature = hmac.new(b'webhook-secret', body, 'sha256').hexdigest()
                delivered = self.client.post(f'{url}/webhook', content=body, headers={
                    'X-Razorpay-Signature': webhook_signature, 'Content-Type': 'application/json',
                })
                self.assertEqual(delivered.status_code, expected_status, delivered.text)
            verified = self.client.post(f'{url}/verify', params=scope, json=callback)
            self.assertEqual(verified.status_code, 200, verified.text)
            self.assertEqual(verified.json(), {'status': 'success', 'data': {
                'payment_status': 'captured', 'razorpay_order_id': 'order_example',
                'razorpay_payment_id': 'pay_example',
            }})

        paid = self.client.get(base, params=scope | {'student_id': student_id}).json()['data'][0]
        self.assertEqual(carry.paid_amount, Decimal('20'))
        self.assertEqual((paid['paid_amount'], paid['due_amount'], paid['payment_mode']), ('100.00', '0.00', 'Razorpay'))
        late_fee = self.client.get('/schools/fee_payments', params=scope | {'student_id': student_id}).json()['late_fee']
        self.assertEqual((late_fee['paid_amount'], late_fee['due_amount']), ('5.50', '0.00'))

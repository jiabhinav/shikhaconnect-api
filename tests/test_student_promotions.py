import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import catalog_setup
import test_sessions
from models.class_section import SchoolClass, Section
from models.generation_settings import FeeGenerationSettings
from models.student import Student, StudentLogin
from models.student_carry_forward import StudentCarryForward
from models.student_fee import (
    StudentFee, StudentFeeChequePayment, StudentFeeLatePayment, StudentFeeRazorpayOrder,
)
from models.student_fee_adjustment import StudentFeeAdjustment
import test_student_fee_adjustments
import test_student_carry_forwards


class StudentPromotionTests(unittest.TestCase):
    setUp = catalog_setup.setUp
    tearDown = test_sessions.SessionTests.tearDown

    scope = {"school_id": 1, "session_id": 1}
    destination_scope = {"school_id": 1, "session_id": 3}
    promotion_url = "/schools/students/promote"

    def prepare_student(self):
        source_id = test_student_carry_forwards.StudentCarryForwardBulkTests.make_student(self, 1, 1, "001")
        source = self.db.get(Student, source_id)
        source.login = StudentLogin(school_id=1, mobile="123456", password="test")
        source.admission_sequence = 1
        target_class = SchoolClass(**self.destination_scope, name="Class 2", class_order=2)
        self.db.add(target_class)
        self.db.commit()
        return source, {"student_id": source_id, "destination_session_id": 3, "class_id": target_class.id}

    def test_transfers_net_balance_and_cancels(self):
        self.db.autoflush = False
        source, payload = self.prepare_student()
        scope = self.scope | {"student_id": source.id}
        carry = self.db.query(StudentCarryForward).filter_by(**scope).one()
        carry.amount = Decimal("40")
        carry.paid_amount = Decimal("10")
        test_student_fee_adjustments.StudentFeeDiscountTests.add_monthly_fee_head(self, source.id, "2025-01", 100)
        self.db.flush()
        fee = self.db.query(StudentFee).filter_by(student_id=source.id).one()
        fee.fee_heads[0].paid_amount = Decimal("20")
        fee.paid_amount = Decimal("35")
        fee.late_fee_amount = Decimal("5")
        self.db.add_all([
            StudentFeeAdjustment(**scope, type="Discount", frequency="One Time",
                                 amount=15, date=date(2025, 1, 1), description="Discount"),
            StudentFeeAdjustment(**scope, type="Penalty", frequency="One Time",
                                 amount=12, paid_amount=2, date=date(2025, 1, 1), description="Penalty"),
            FeeGenerationSettings(**self.scope, generation_day=1, payment_due_day=9,
                                  late_fee_type="daily", late_fee_amount=4, min_due_amount=1),
        ])
        self.db.commit()

        with patch("routers.student_promotions.today", return_value=date(2025, 1, 13)):
            dues = self.client.get("/schools/students/StudentPromoteList", params=scope | {"status": "active"})
            result = self.client.post(self.promotion_url, params=self.scope, json=payload)
        operation = self.client.app.openapi()["paths"]["/schools/students/StudentPromoteList"]["get"]
        self.assertEqual(operation["operationId"], "StudentPromoteList")
        self.assertEqual(dues.status_code, 200, dues.text)
        self.assertEqual(dues.json()["total_records"], 1)
        self.assertEqual(dues.json()["data"][0]["student_id"], source.id)
        self.assertEqual(result.status_code, 201, result.text)
        body = result.json()
        self.assertEqual({key: dues.json()["data"][0][key] for key in body["balance"]}, body["balance"])
        self.assertEqual(body["active_session_id"], 3)
        self.assertEqual({key: Decimal(value) for key, value in body["balance"].items()}, {
            "fee_head_due_after_discounts": Decimal("65"),
            "discount_amount": Decimal("15"),
            "penalty_due": Decimal("10"),
            "carry_forward_due": Decimal("30"),
            "late_fee_due": Decimal("7"),
            "total": Decimal("112"),
            "final_due_amount": Decimal("112"),
        })
        target_id = body["destination_student_id"]
        history = self.client.get("/schools/students/StudentPromoteList", params=scope | {"status": "promoted"})
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["data"][0]["transferred_to_student_id"], target_id)
        previous = self.client.get(f"/schools/1/students/{source.id}")
        self.assertEqual(previous.status_code, 200, previous.text)
        self.assertEqual(previous.json()["data"]["student_info"]["status"], "promoted")
        active_list = self.client.get("/schools/students/StudentPromoteList", params=self.scope | {"status": "active"})
        self.assertEqual(active_list.status_code, 200, active_list.text)
        self.assertEqual(active_list.json()["data"], [])
        self.assertEqual(self.client.get("/schools/students/StudentPromoteList", params=scope | {"session_id": 3}).status_code, 404)

        target = self.db.get(Student, target_id)
        self.assertEqual((target.status, source.status), ("active", "promoted"))
        self.assertEqual((target.first_name, target.father_name, target.login_id),
                         (source.first_name, source.father_name, source.login_id))
        self.assertIsNone(target.admission_sequence)
        target_carry = self.db.query(StudentCarryForward).filter_by(student_id=target_id, session_id=3).one()
        self.assertEqual(target_carry.amount, 112)
        self.assertEqual(body["carry_forward"], {"id": target_carry.id, "month": "2026-01", "amount": "112.00"})
        listed_carry = self.client.get(
            "/schools/student_carry_forwards", params=self.destination_scope | {"student_id": target_id},
        )
        self.assertEqual(listed_carry.status_code, 200, listed_carry.text)
        self.assertEqual(listed_carry.json()["data"][0]["amount"], "112.00")
        deletion = self.client.delete("/schools/students", params={"school_id": 1, "student_id": target_id})
        self.assertEqual(deletion.status_code, 409, deletion.text)
        self.assertIn("Cancel the promotion", deletion.json()["detail"])
        self.assertEqual(self.client.post(self.promotion_url, params=self.scope, json=payload).status_code, 409)

        payment = {"student_id": source.id, "payment_mode": "Cash",
                   "months": [{"fee_month": "2025-01", "payment_amount": 1}]}
        self.assertEqual(self.client.post("/schools/student_fees", params=self.scope, json=payment).status_code, 409)

        cancellation = self.client.post(f"{self.promotion_url}/cancel", params=self.scope, json={
            "student_id": source.id, "destination_student_id": target_id,
        })
        self.assertEqual(cancellation.status_code, 200, cancellation.text)
        self.assertEqual(cancellation.json()["active_session_id"], 1)
        self.assertEqual(source.status, "active")
        self.assertIsNone(self.db.get(Student, target_id))
        self.assertIsNone(self.db.query(StudentCarryForward).filter_by(student_id=target_id).one_or_none())
        self.assertEqual(carry.unpaid_amount, 30)

    def test_cannot_cancel_after_destination_payment(self):
        source, payload = self.prepare_student()
        carry = self.db.query(StudentCarryForward).filter_by(student_id=source.id).one()
        carry.amount = Decimal("30")
        self.db.commit()

        result = self.client.post(self.promotion_url, params=self.scope, json=payload)
        self.assertEqual(result.status_code, 201, result.text)
        target_id = result.json()["destination_student_id"]
        payment = self.client.post("/schools/student_fees", params=self.destination_scope, json={
            "student_id": target_id, "payment_mode": "Cash",
            "months": [{"fee_month": "2026-01", "payment_amount": 1}],
        })
        self.assertEqual(payment.status_code, 201, payment.text)

        cancellation = self.client.post(f"{self.promotion_url}/cancel", params=self.scope, json={
            "student_id": source.id, "destination_student_id": target_id,
        })
        self.assertEqual(cancellation.status_code, 409, cancellation.text)
        self.assertEqual(source.status, "promoted")

    def test_transfers_legacy_principal_and_recorded_late_fee(self):
        source, payload = self.prepare_student()
        scope = self.scope | {"student_id": source.id}
        self.db.add(StudentFee(
            school_id=1, academic_session_id=1, student_id=source.id, fee_month="2025-01",
            total_amount=120, net_amount=100, discount_amount=20, paid_amount=35,
            late_fee_amount=5, status="PARTIAL",
        ))
        self.db.add(StudentFeeLatePayment(
            **scope, fee_month="2025-01", due_date=date(2025, 1, 10), overdue_days=3,
            amount=15, paid_amount=5,
        ))
        self.db.commit()

        result = self.client.post(self.promotion_url, params=self.scope, json=payload)
        self.assertEqual(result.status_code, 201, result.text)
        balance = result.json()["balance"]
        self.assertEqual(Decimal(balance["fee_head_due_after_discounts"]), 70)
        self.assertEqual(Decimal(balance["late_fee_due"]), 10)
        self.assertEqual(Decimal(balance["total"]), 80)

    def test_bulk_promote_and_cancel_atomically(self):
        first, payload = self.prepare_student()
        second_id = test_student_carry_forwards.StudentCarryForwardBulkTests.make_student(self, 1, 1, "002")
        second = payload | {"student_id": second_id, "status": "inactive"}
        failed = self.client.post(self.promotion_url, params=self.scope, json=[
            payload, second | {"class_id": 999},
        ])
        self.assertEqual(failed.status_code, 422, failed.text)
        self.assertEqual(failed.json()["detail"]["row"], 2)
        self.assertEqual(first.status, "active")
        self.assertEqual(self.db.query(Student).filter_by(session_id=3).count(), 0)

        response = self.client.post(self.promotion_url, params=self.scope, json=[second, payload])
        self.assertEqual(response.status_code, 201, response.text)
        rows = response.json()
        self.assertEqual([row["source_student_id"] for row in rows], [second_id, first.id])

        self.assertEqual([row["source_status"] for row in rows], ["promoted", "promoted"])
        self.assertEqual([row["destination_status"] for row in rows], ["inactive", "active"])
        target_id = rows[0]["destination_student_id"]
        zero_carry = self.client.get(
            "/schools/student_carry_forwards", params=self.destination_scope | {"student_id": target_id},
        )
        self.assertEqual(zero_carry.status_code, 200, zero_carry.text)
        self.assertEqual(Decimal(zero_carry.json()["data"][0]["amount"]), 0)
        self.assertIsNone(rows[0]["active_session_id"])

        cancel = [
            {"student_id": row["source_student_id"], "destination_student_id": row["destination_student_id"]}
            for row in rows
        ]
        response = self.client.post(f"{self.promotion_url}/cancel", params=self.scope, json=cancel)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["active_student_id"] for row in response.json()], [second_id, first.id])
        self.assertEqual([row["source_status"] for row in response.json()], ["active", "active"])
        self.assertTrue(all(row["destination_status"] is None for row in response.json()))
        self.assertEqual(self.db.query(Student).filter_by(session_id=3).count(), 0)


    def test_list_filters_by_selected_section_and_search(self):
        first, _ = self.prepare_student()
        second_id = test_student_carry_forwards.StudentCarryForwardBulkTests.make_student(self, 1, 1, "002")
        section_a = Section(**self.scope, name="A")
        section_b = Section(**self.scope, name="B")
        self.db.add_all([section_a, section_b])
        self.db.flush()
        first.section_id = section_a.id
        first.roll_number = "17A"
        self.db.get(Student, second_id).section_id = section_b.id
        self.db.commit()
        params = self.scope | {"class_id": first.class_id, "section_id": section_a.id, "status": "active"}

        for search in ("17A", "001", "Student"):
            response = self.client.get("/schools/students/StudentPromoteList", params=params | {"search": search})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["total_records"], 1)
            row, = response.json()["data"]
            self.assertEqual((row["student_id"], row["roll_number"], row["father_name"]),
                             (first.id, "17A", first.father_name))
        response = self.client.get("/schools/students/StudentPromoteList", params=params | {"search": "002"})
        self.assertEqual(response.json()["data"], [])

    def test_cannot_promote_into_same_session_or_duplicate_destination_admission(self):
        source, payload = self.prepare_student()
        same_session = self.client.post(self.promotion_url, params=self.scope, json=payload | {
            "destination_session_id": source.session_id,
            "class_id": source.class_id,
        })
        self.assertEqual(same_session.status_code, 422, same_session.text)
        self.assertEqual(source.status, "active")
        self.assertEqual(self.db.query(Student).count(), 1)

        existing_id = test_student_carry_forwards.StudentCarryForwardBulkTests.make_student(
            self, 1, 3, source.admission_number,
        )
        duplicate = self.client.post(self.promotion_url, params=self.scope, json=payload)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(source.status, "active")
        self.assertEqual([row.id for row in self.db.query(Student).filter_by(session_id=3)], [existing_id])


    def test_pending_payment_error_identifies_each_blocker(self):
        source, payload = self.prepare_student()
        scope = self.scope | {"student_id": source.id}
        cheque = StudentFeeChequePayment(**scope, cheque_number="101", months=[])
        order = StudentFeeRazorpayOrder(**scope, razorpay_order_id="order_test", amount=100, months=[])
        for pending_cheque, unposted_order in ((True, False), (True, True), (False, True)):
            if pending_cheque:
                self.db.add(cheque)
            else:
                cheque.status = "BOUNCED"
            if unposted_order:
                self.db.add(order)
            self.db.commit()
            response = self.client.post(self.promotion_url, params=self.scope, json=[payload])
            self.assertEqual(response.status_code, 409, response.text)
            detail = response.json()["detail"]
            self.assertEqual(detail["student_id"], source.id)
            self.assertEqual("Pending cheque payments" in detail["message"], pending_cheque)
            self.assertEqual("Unposted Razorpay orders" in detail["message"], unposted_order)


    def test_promoted_request_status_creates_active_destination(self):
        source, payload = self.prepare_student()
        response = self.client.post(
            self.promotion_url, params=self.scope, json=payload | {"status": "promoted"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["source_status"], "promoted")
        self.assertEqual(result["destination_status"], "active")
        self.assertEqual(self.db.get(Student, source.id).status, "promoted")
        self.assertEqual(self.db.get(Student, result["destination_student_id"]).status, "active")

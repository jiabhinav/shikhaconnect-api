import hmac
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.student_fee import StudentFeeRazorpayOrder
from models.user import User, UserRole
from routers.fee_assignments import apply_student_fee_payment
from routers.fee_structures import fee_structure_school
from schemas.enums import PaymentMode
from schemas.student_fee import StudentFeeMonthPayment, StudentFeeWrite
from utils.razorpay import RazorpayClient, RazorpaySettings

router = APIRouter(prefix="/student_fees/razorpay", tags=["Configure Fees"])


class RazorpayOrderWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: int = Field(gt=0)
    months: list[StudentFeeMonthPayment] = Field(max_length=12)
    late_fee_payment_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=16, decimal_places=2)

    @model_validator(mode="after")
    def validate_allocations(self):
        if not self.months:
            raise ValueError("Select at least one fee month")
        if len({month.fee_month for month in self.months}) != len(self.months):
            raise ValueError("fee_month values must be unique")
        payment = StudentFeeWrite(
            student_id=self.student_id, months=self.months, late_fee_payment_amount=self.late_fee_payment_amount,
        )
        if any(month.payment_amount + payment.late_fee_payment_for(month) <= 0 for month in self.months):
            raise ValueError("Razorpay payment amounts must be greater than zero")
        return self


class RazorpayVerifyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    razorpay_order_id: str = Field(pattern=r"^order_[a-zA-Z0-9]+$", max_length=100)
    razorpay_payment_id: str = Field(pattern=r"^pay_[a-zA-Z0-9]+$", max_length=100)
    razorpay_signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class RazorpayOrderData(BaseModel):
    order_id: str
    amount: int = Field(description="Amount in paise")
    currency: Literal["INR"] = "INR"
    key_id: str


class RazorpayOrderResponse(BaseModel):
    status: Literal["success"] = "success"
    data: RazorpayOrderData


class RazorpayPaymentData(BaseModel):
    payment_status: Literal["captured"] = "captured"
    razorpay_order_id: str
    razorpay_payment_id: str


class RazorpayPaymentResponse(BaseModel):
    status: Literal["success"] = "success"
    data: RazorpayPaymentData


def razorpay_school(
    school_id: int,
    db: Session = Depends(fee_structure_school),
    user: User = Depends(get_current_user),
):
    if user.role != UserRole.SUPER_ADMIN:
        if user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN) or db.query(SchoolMapping.id).filter_by(
            school_id=school_id, user_id=user.login_user_id, status=SchoolMappingStatus.ACTIVE,
        ).first() is None:
            raise HTTPException(403, "You cannot collect payments for this school")
    return db


def verify_signature(secret, message, signature):
    expected = hmac.new(secret.encode(), message, "sha256").hexdigest().encode()
    if not hmac.compare_digest(expected, signature.encode()):
        raise HTTPException(400, "Invalid Razorpay signature")


@router.post("/orders", status_code=201, response_model=RazorpayOrderResponse)
def create_order(
    school_id: int,
    session_id: int,
    payload: RazorpayOrderWrite,
    db: Annotated[Session, Depends(razorpay_school)],
):
    client = RazorpayClient(RazorpaySettings())
    payment = StudentFeeWrite(
        student_id=payload.student_id, months=payload.months, late_fee_payment_amount=payload.late_fee_payment_amount,
    )
    apply_student_fee_payment(
        db, school_id, session_id, payment, validate_only=True, gateway_mode=PaymentMode.RAZORPAY,
    )
    amount = int(sum((month.payment_amount for month in payload.months), payment.total_late_fee_payment) * 100)
    created = client.request("POST", "/v1/orders", {
        "amount": amount, "currency": "INR", "receipt": uuid4().hex, "partial_payment": False,
    })
    order_id = created.get("id")
    if (
        not isinstance(order_id, str) or not re.fullmatch(r"order_[a-zA-Z0-9]{1,94}", order_id)
        or created.get("currency") != "INR" or created.get("amount") != amount
    ):
        raise HTTPException(502, "Razorpay returned an invalid order")
    db.add(StudentFeeRazorpayOrder(
        school_id=school_id, session_id=session_id, student_id=payload.student_id,
        razorpay_order_id=order_id, amount=amount,
        months=[month.model_dump(mode="json") for month in payload.months],
        late_fee_payment_amount=payload.late_fee_payment_amount,
    ))
    db.commit()
    return {"data": {"order_id": order_id, "key_id": client.key_id, "amount": amount, "currency": "INR"}}


@router.post("/verify", response_model=RazorpayPaymentResponse)
def verify_payment(
    school_id: int,
    session_id: int,
    payload: RazorpayVerifyWrite,
    db: Annotated[Session, Depends(razorpay_school)],
):
    order = db.query(StudentFeeRazorpayOrder).filter_by(
        school_id=school_id, session_id=session_id, razorpay_order_id=payload.razorpay_order_id,
    ).with_for_update().first()
    if order is None:
        raise HTTPException(404, "Razorpay order not found")
    client = RazorpayClient(RazorpaySettings())
    message = f"{order.razorpay_order_id}|{payload.razorpay_payment_id}".encode()
    verify_signature(client.secret, message, payload.razorpay_signature)
    if order.posted_at is not None:
        if order.razorpay_payment_id != payload.razorpay_payment_id:
            raise HTTPException(409, "The order was already posted with another payment")
    else:
        payment = client.request("GET", f"/v1/payments/{payload.razorpay_payment_id}")
        if payment.get("id") != payload.razorpay_payment_id:
            raise HTTPException(502, "Razorpay returned a different payment")
        post_captured_payment(db, order, payment)
    return {"data": {
        "razorpay_order_id": payload.razorpay_order_id,
        "razorpay_payment_id": payload.razorpay_payment_id,
    }}


def post_captured_payment(db, order, payment):
    payment_id = payment.get("id")
    if (
        not isinstance(payment_id, str) or not re.fullmatch(r"pay_[a-zA-Z0-9]{1,96}", payment_id)
        or payment.get("order_id") != order.razorpay_order_id
        or payment.get("currency") != "INR" or payment.get("amount") != order.amount
    ):
        raise HTTPException(409, "Razorpay payment does not match the stored order")
    if order.posted_at is not None:
        if order.razorpay_payment_id != payment_id:
            raise HTTPException(409, "The order was already posted with another payment")
        return
    if payment.get("status") != "captured" or payment.get("amount_refunded", 0) != 0:
        raise HTTPException(409, "Razorpay payment must be captured and unrefunded")
    try:
        allocation = StudentFeeWrite(
            student_id=order.student_id, months=order.months, late_fee_payment_amount=order.late_fee_payment_amount,
        )
        apply_student_fee_payment(
            db, order.school_id, order.session_id, allocation, gateway_mode=PaymentMode.RAZORPAY,
        )
        order.razorpay_payment_id = payment_id
        order.posted_at = datetime.now(timezone.utc)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Razorpay payment could not be posted to the fee ledger") from exc
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(409, "The stored order requires explicit late-fee amounts for its fee months") from exc
    except Exception:
        db.rollback()
        raise


@router.post("/webhook", status_code=204)
async def razorpay_webhook(request: Request, db: Annotated[Session, Depends(get_db_session)]):
    secret = RazorpaySettings().razorpay_webhook_secret.get_secret_value()
    if not secret:
        raise HTTPException(503, "Razorpay webhook is not configured")
    body = await request.body()
    verify_signature(secret, body, request.headers.get("X-Razorpay-Signature", ""))
    try:
        event = json.loads(body)
        if event["event"] != "payment.captured":
            return Response(status_code=204)
        payment = event["payload"]["payment"]["entity"]
        order_id = payment["order_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, "Invalid Razorpay webhook payload") from exc
    await run_in_threadpool(post_webhook_payment, db, order_id, payment)
    return Response(status_code=204)


def post_webhook_payment(db, order_id, payment):
    order = db.query(StudentFeeRazorpayOrder).filter_by(razorpay_order_id=order_id).with_for_update().first()
    if order is not None:
        post_captured_payment(db, order, payment)

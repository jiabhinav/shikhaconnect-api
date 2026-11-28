from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, PositiveInt, computed_field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.class_section import ClassSectionMapping, SchoolClass
from models.school import School
from models.session import Session as SchoolSession
from models.student import PreviousSchool, Student, StudentAddressRecord
from models.student_carry_forward import StudentCarryForward
from models.student_document import StudentDocument
from models.student_fee import (
    StudentFee, StudentFeeChequePayment, StudentFeeLatePayment, StudentFeeRazorpayOrder, StudentFeeReceipt,
)
from models.student_fee_adjustment import StudentFeeAdjustment
from models.student_promotion import StudentPromotion
from routers.fee_assignments import fee_payment_months, late_fee_settings
from routers.student_carry_forwards import carry_forward_db
from schemas.enums import LateFeeType, StudentStatus
from utils.dates import today

router = APIRouter()
ZERO = Decimal("0.00")


class StudentPromotionWrite(BaseModel):
    student_id: PositiveInt
    destination_session_id: PositiveInt
    class_id: PositiveInt
    section_id: PositiveInt | None = None
    roll_number: str | None = Field(default=None, max_length=50)
    status: Literal[StudentStatus.ACTIVE, StudentStatus.INACTIVE, StudentStatus.PROMOTED] = Field(
        default=StudentStatus.ACTIVE,
        description="Destination status. Passing promoted creates an active destination and marks the source promoted.",
    )


class StudentPromotionCancel(BaseModel):
    student_id: PositiveInt
    destination_student_id: PositiveInt


class PromotionBalance(BaseModel):
    fee_head_due_after_discounts: Decimal
    discount_amount: Decimal
    penalty_due: Decimal
    carry_forward_due: Decimal
    late_fee_due: Decimal
    total: Decimal

    @computed_field(description="Final outstanding balance after discounts and recorded payments.")
    @property
    def final_due_amount(self) -> Decimal:
        return self.total


class PromotionCarryForward(BaseModel):
    id: int
    month: str
    amount: Decimal


class PromotionResult(BaseModel):
    source_student_id: int
    destination_student_id: int
    source_session_id: int
    destination_session_id: int
    source_status: StudentStatus
    destination_status: StudentStatus | None
    active_student_id: int | None
    active_session_id: int | None
    balance: PromotionBalance | None = None
    carry_forward: PromotionCarryForward | None = None


class StudentDuesRow(PromotionBalance):
    student_id: int
    student_name: str
    father_name: str
    roll_number: str | None
    admission_number: str | None
    class_id: int
    section_id: int | None
    status: StudentStatus
    transferred_to_student_id: int | None


class StudentDuesResult(BaseModel):
    session_id: int
    as_of: date
    total_records: int
    data: list[StudentDuesRow]


@router.get("/students/dues", response_model=StudentDuesResult, include_in_schema=False)
@router.get(
    "/students/StudentPromoteList",
    response_model=StudentDuesResult,
    operation_id="StudentPromoteList",
    summary="StudentPromoteList",
)
def list_student_dues(
    school_id: int,
    session_id: int,
    student_id: PositiveInt | None = None,
    class_id: PositiveInt | None = None,
    section_id: PositiveInt | None = None,
    status: StudentStatus | None = None,
    search: str | None = Query(None, min_length=1, max_length=250),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(carry_forward_db),
):
    """List session dues using the same calculation as student promotion."""
    session = db.get(SchoolSession, session_id)
    as_of = min(today(), session.end_date)
    query = (
        db.query(Student, StudentPromotion.destination_student_id)
        .outerjoin(StudentPromotion, StudentPromotion.source_student_id == Student.id)
        .filter(Student.school_id == school_id, Student.session_id == session_id)
    )
    for column, value in (
        (Student.id, student_id), (Student.class_id, class_id), (Student.section_id, section_id),
        (Student.status, status.value if status else None),
    ):
        if value is not None:
            query = query.filter(column == value)
    if search is not None:
        term = search.strip()
        if not term:
            raise HTTPException(422, "search must not be blank")
        name_match = and_(*(or_(
            Student.first_name.icontains(part, autoescape=True),
            Student.middle_name.icontains(part, autoescape=True),
            Student.last_name.icontains(part, autoescape=True),
        ) for part in term.split()))
        query = query.filter(or_(
            name_match,
            Student.roll_number.icontains(term, autoescape=True),
            Student.admission_number.icontains(term, autoescape=True),
        ))
    total_records = query.count()
    if student_id is not None and not total_records:
        raise HTTPException(404, "Student not found for the selected session and filters")

    data = []
    for student, transferred_to in query.order_by(Student.id).offset(offset).limit(limit):
        balance = promotion_balance(db, placement_scope(student), session, as_of, lock_carry=False)
        data.append(StudentDuesRow(
            **balance.model_dump(exclude={"final_due_amount"}),
            student_id=student.id,
            student_name=" ".join(filter(None, (student.first_name, student.middle_name, student.last_name))),
            father_name=student.father_name,
            roll_number=student.roll_number,
            admission_number=student.admission_number,
            class_id=student.class_id,
            section_id=student.section_id,
            status=student.status,
            transferred_to_student_id=transferred_to,
        ))
    return StudentDuesResult(session_id=session_id, as_of=as_of, total_records=total_records, data=data)


def locked_student(db, school_id, session_id, student_id):
    item = db.query(Student).filter_by(
        school_id=school_id, session_id=session_id, id=student_id,
    ).with_for_update().one_or_none()
    if item is None:
        raise HTTPException(404, "Student not found in the source session")
    return item


def promotion_balance(db, scope, session, as_of, *, lock_carry=True):
    carry_query = db.query(StudentCarryForward).filter_by(**scope)
    if lock_carry:
        carry_query = carry_query.with_for_update()
    carry = carry_query.one_or_none()
    balances = fee_payment_months(db, **scope, include_pending_discounts=True, for_promotion=True)
    settings = late_fee_settings(db, scope)
    recorded_late_fees = {
        row.fee_month: row for row in db.query(StudentFeeLatePayment).filter_by(**scope)
    }
    carry_due = carry.amount - carry.paid_amount if carry else ZERO
    penalty_due = sum((month.penalty_due_amount for month in balances), ZERO)
    principal_due = sum((
        max(ZERO, month.due_amount - month.penalty_due_amount
            - (month.carry_forward.due_amount if month.carry_forward else ZERO))
        for month in balances
    ), ZERO)
    late_due = sum((
        late_fee_due(month, settings, recorded_late_fees.get(month.fee_month), session, as_of)
        for month in balances
    ), ZERO)
    return PromotionBalance(
        fee_head_due_after_discounts=principal_due,
        discount_amount=sum((month.total_discount_amount for month in balances), ZERO),
        penalty_due=penalty_due, carry_forward_due=carry_due, late_fee_due=late_due,
        total=principal_due + penalty_due + carry_due + late_due,
    )


def late_fee_due(month, settings, recorded, session, as_of):
    if recorded is not None:
        # The same payment can appear on both the late-fee ledger and monthly record.
        return max(ZERO, recorded.amount - max(recorded.paid_amount, month.late_fee_paid_amount))
    if not settings or not settings.late_fee_enabled or not settings.late_fee_amount or not settings.late_fee_type:
        return ZERO
    year, month_number = map(int, month.fee_month.split("-"))
    day = min(settings.generation_day, monthrange(year, month_number)[1])
    generation = max(date(year, month_number, day), session.start_date)
    overdue_days = (as_of - generation).days - settings.payment_due_day
    threshold = (month.payable_amount - month.late_fee_paid_amount) * (settings.min_due_amount or 0) / 100
    if overdue_days <= 0 or month.due_amount <= 0 or month.due_amount < threshold:
        return ZERO
    multiplier = overdue_days if settings.late_fee_type == LateFeeType.DAILY else 1
    return max(ZERO, settings.late_fee_amount * multiplier - month.late_fee_paid_amount)


def copy_columns(instance, exclude):
    return {
        column.key: getattr(instance, column.key)
        for column in instance.__table__.columns if column.key not in exclude
    }


def promotion_result(source, destination, active, balance=None, *, carry_forward=None, cancelled=False):
    return PromotionResult(
        source_student_id=source.id, destination_student_id=destination.id,
        source_session_id=source.session_id, destination_session_id=destination.session_id,
        source_status=source.status, destination_status=None if cancelled else destination.status,
        active_student_id=active.id if active.status == StudentStatus.ACTIVE else None,
        active_session_id=active.session_id if active.status == StudentStatus.ACTIVE else None,
        balance=balance, carry_forward=carry_forward,
    )


def save_promotions(db, school_id, session_id, payload, operation, conflict_message):
    is_batch = isinstance(payload, list)
    items = payload if is_batch else [payload]
    if len({item.student_id for item in items}) != len(items):
        raise HTTPException(422, "Each student can appear only once in a batch")

    try:
        # Match student edits by taking the school lock before any student locks.
        db.query(School.id).filter_by(id=school_id).with_for_update(read=True, key_share=True).one()
        results = {}
        for index, item in sorted(enumerate(items), key=lambda entry: entry[1].student_id):
            try:
                results[index] = operation(db, school_id, session_id, item)
                db.flush()
            except IntegrityError as exc:
                detail = {"row": index + 1, "student_id": item.student_id, "message": conflict_message}
                raise HTTPException(409, detail if is_batch else conflict_message) from exc
            except HTTPException as exc:
                if not is_batch:
                    raise
                detail = {"row": index + 1, "student_id": item.student_id, "message": exc.detail}
                raise HTTPException(exc.status_code, detail) from exc
        db.commit()
    except Exception:
        db.rollback()
        raise
    return [results[index] for index in range(len(items))] if is_batch else results[0]


@router.post("/students/promote", status_code=201, response_model=PromotionResult | list[PromotionResult])
def promote_student(
    school_id: int,
    session_id: int,
    payload: StudentPromotionWrite | Annotated[list[StudentPromotionWrite], Field(min_length=1, max_length=1000)],
    db: Session = Depends(carry_forward_db),
):
    """Promote one student or an array of students in a single transaction."""
    return save_promotions(
        db, school_id, session_id, payload, promote_one,
        "Promotion conflicts with an existing student or destination admission number",
    )


def promote_one(db, school_id, session_id, payload):
    source = locked_student(db, school_id, session_id, payload.student_id)
    if source.status != StudentStatus.ACTIVE.value or db.get(StudentPromotion, source.id) is not None:
        raise HTTPException(409, "Only an active student can be promoted once")
    source_session = db.get(SchoolSession, session_id)
    destination_session = db.query(SchoolSession).filter_by(
        id=payload.destination_session_id, school_id=school_id,
    ).one_or_none()
    if destination_session is None:
        raise HTTPException(404, "Destination session not found in this school")
    if destination_session.id == source_session.id:
        raise HTTPException(422, "Source and destination sessions must be different")
    if destination_session.start_date <= source_session.start_date:
        raise HTTPException(422, "Destination session must start after the source session starts")
    placement = dict(school_id=school_id, session_id=destination_session.id, class_id=payload.class_id)
    if db.query(SchoolClass.id).filter_by(
        id=payload.class_id, school_id=school_id, session_id=destination_session.id,
    ).first() is None:
        raise HTTPException(422, "Class must belong to the destination session")
    if payload.section_id is not None and db.query(ClassSectionMapping.id).filter_by(
        **placement, section_id=payload.section_id,
    ).first() is None:
        raise HTTPException(422, "Section must be assigned to the class in the destination session")

    scope = dict(school_id=school_id, session_id=session_id, student_id=source.id)
    pending_cheques = db.query(StudentFeeChequePayment.id).filter_by(**scope, status="PENDING").count()
    unposted_orders = db.query(StudentFeeRazorpayOrder.id).filter_by(**scope, posted_at=None).count()
    if pending_cheques or unposted_orders:
        blockers = []
        if pending_cheques:
            blockers.append(f"Pending cheque payments ({pending_cheques}). Record the outcome of each cheque.")
        if unposted_orders:
            blockers.append(f"Unposted Razorpay orders ({unposted_orders}). Verify their payment status in Razorpay.")
        raise HTTPException(409, "Promotion blocked. " + " ".join(blockers))

    balance = promotion_balance(db, scope, source_session, min(today(), source_session.end_date))
    if balance.total > Decimal("9999999999.99"):
        raise HTTPException(422, "Calculated balance exceeds the carry-forward amount limit")

    destination_status = StudentStatus.ACTIVE if payload.status == StudentStatus.PROMOTED else payload.status
    destination = Student(**copy_columns(source, {
        "id", "session_id", "class_id", "section_id", "roll_number", "status", "admission_sequence",
    }), session_id=destination_session.id, class_id=payload.class_id, section_id=payload.section_id,
        roll_number=payload.roll_number, status=destination_status.value)
    destination.addresses = [
        StudentAddressRecord(**copy_columns(address, {"id", "student_id"}))
        for address in source.addresses
    ]
    destination.documents = [
        StudentDocument(**copy_columns(document, {"id", "student_id"}))
        for document in source.documents
    ]
    if source.previous_school is not None:
        destination.previous_school = PreviousSchool(**copy_columns(source.previous_school, {"id", "student_id"}))

    db.add(destination)
    db.flush()
    carry = db.query(StudentCarryForward).filter_by(**placement_scope(destination)).one()
    carry.amount = balance.total
    db.add(StudentPromotion(school_id=school_id, source_student_id=source.id, destination_student_id=destination.id))
    source.status = StudentStatus.PROMOTED.value
    return promotion_result(
        source, destination, destination, balance,
        carry_forward=PromotionCarryForward(id=carry.id, month=carry.month, amount=carry.amount),
    )


def placement_scope(student):
    return dict(school_id=student.school_id, session_id=student.session_id, student_id=student.id)


@router.post("/students/promote/cancel", response_model=PromotionResult | list[PromotionResult])
def cancel_student_promotion(
    school_id: int,
    session_id: int,
    payload: StudentPromotionCancel | Annotated[list[StudentPromotionCancel], Field(min_length=1, max_length=1000)],
    db: Session = Depends(carry_forward_db),
):
    """Cancel one promotion or an array of promotions in a single transaction."""
    return save_promotions(
        db, school_id, session_id, payload, cancel_one,
        "The destination student is referenced by other records",
    )


def cancel_one(db, school_id, session_id, payload):
    source = locked_student(db, school_id, session_id, payload.student_id)
    promotion = db.get(StudentPromotion, source.id)
    if promotion is None or promotion.destination_student_id != payload.destination_student_id:
        raise HTTPException(404, "Matching student promotion not found")
    destination = db.query(Student).filter_by(
        id=promotion.destination_student_id, school_id=school_id,
    ).with_for_update().one()
    if db.get(StudentPromotion, destination.id) is not None:
        raise HTTPException(409, "Cancel the student's later promotion first")

    scope = placement_scope(destination)
    carry = db.query(StudentCarryForward).filter_by(**scope).with_for_update().one_or_none()
    fee = db.query(StudentFee.id).filter_by(
        school_id=school_id, academic_session_id=destination.session_id, student_id=destination.id,
    ).first()
    if fee or (carry and carry.paid_amount > 0) or any(
        db.query(model.id).filter_by(**scope).first()
        for model in (StudentFeeAdjustment, StudentFeeLatePayment, StudentFeeReceipt,
                      StudentFeeChequePayment, StudentFeeRazorpayOrder)
    ):
        raise HTTPException(409, "The destination record has fee activity and cannot be cancelled")

    db.delete(promotion)
    db.flush()
    if carry is not None:
        db.delete(carry)
    db.delete(destination)
    source.status = StudentStatus.ACTIVE.value
    return promotion_result(source, destination, source, cancelled=True)

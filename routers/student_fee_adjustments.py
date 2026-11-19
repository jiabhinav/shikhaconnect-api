from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import PositiveInt
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.class_section import SchoolClass, Section
from models.session import Session as SchoolSession
from models.student import Student
from models.student_fee import StudentFee, StudentFeeHead
from models.student_fee_adjustment import StudentFeeAdjustment
from routers.fee_assignments import fee_payment_months
from routers.fee_structures import fee_structure_school
from schemas.enums import FeeAdjustmentFrequency, FeeAdjustmentType, FeeDiscountType
from schemas.student_fee_adjustment import (
    FeeAdjustmentDropdownResult,
    FeeAdjustmentStudentListResult,
    StudentFeeAdjustmentBulkResult,
    StudentFeeAdjustmentCreateResult,
    StudentFeeAdjustmentListResult,
    StudentFeeAdjustmentResponse,
    StudentFeeAdjustmentResult,
    StudentFeeAdjustmentWrite,
)
from utils.fee_months import session_months

router = APIRouter(prefix="/student_fee_adjustments", tags=["Configure Fees"])
AllFilter = Literal["all", "All", "ALL"]


@router.get("/dropdowns", response_model=FeeAdjustmentDropdownResult)
def get_fee_adjustment_dropdowns():
    return {
        "message": "Fee adjustment dropdowns fetched successfully",
        "data": {
            "types": list(FeeAdjustmentType),
            "frequencies": list(FeeAdjustmentFrequency),
            "discount_types": list(FeeDiscountType),
        },
    }


def adjustment_scope(
    school_id: int,
    session_id: int,
    student_id: int = Query(gt=0),
    db: Session = Depends(fee_structure_school),
):
    scope = {"school_id": school_id, "session_id": session_id, "student_id": student_id}
    if db.query(Student.id).filter_by(id=student_id, school_id=school_id, session_id=session_id).first() is None:
        raise HTTPException(404, "Student not found for this school session")
    return scope


def find_adjustment(db, scope, adjustment_id, *, lock=False):
    query = db.query(StudentFeeAdjustment).filter_by(**scope, id=adjustment_id)
    if lock:
        query = query.with_for_update()
    item = query.first()
    if item is None:
        raise HTTPException(404, "Student fee adjustment not found")
    return item


def require_editable_adjustment(db, scope, item):
    if item.type == FeeAdjustmentType.DISCOUNT:
        recorded_discount = db.query(StudentFee.id).filter_by(
            school_id=scope["school_id"],
            academic_session_id=scope["session_id"],
            student_id=scope["student_id"],
            fee_month=item.date.strftime("%Y-%m"),
        ).filter(or_(
            StudentFee.discount_amount > 0,
            StudentFee.fee_heads.any(StudentFeeHead.discount_amount > 0),
        )).first()
        if recorded_discount is not None:
            raise HTTPException(409, "A discount is already recorded in the fee payment for this month")
        validate_discount_due(db, scope, [item.date], item.amount, exclude_adjustment_id=item.id)
        return
    if item.applied_at is not None:
        raise HTTPException(409, "Applied student fee adjustments cannot be edited or deleted")


def save_adjustments(db, items):
    db.add_all(items)
    commit_adjustments(db)


def commit_adjustments(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Student fee adjustment conflicts with database constraints") from exc


def lock_adjustment_student(db, scope):
    student = db.query(Student.id).filter_by(
        id=scope["student_id"], school_id=scope["school_id"], session_id=scope["session_id"],
    ).with_for_update().first()
    if student is None:
        raise HTTPException(404, "Student not found for this school session")


def discount_due_failures(db, scope, dates, amount, *, exclude_adjustment_id=None):
    months = [selected.strftime("%Y-%m") for selected in dates]
    balances = fee_payment_months(db, **scope, months=months, exclude_adjustment_id=exclude_adjustment_id)
    failures = []
    for balance in balances:
        if balance.due_amount <= 0 or amount > balance.due_amount:
            message = (
                "All fees are paid for this month" if balance.due_amount <= 0
                else "Discount amount cannot exceed the remaining due"
            )
            failures.append({
                "message": message,
                "fee_month": balance.fee_month,
                "due_amount": str(balance.due_amount),
                "discount_amount": str(amount),
            })
    return failures


def validate_discount_due(db, scope, dates, amount, *, exclude_adjustment_id=None):
    failures = discount_due_failures(db, scope, dates, amount, exclude_adjustment_id=exclude_adjustment_id)
    if failures:
        raise HTTPException(422, failures[0])


def monthly_session_end(db, scope, selected_date, *, by_month=False):
    session = db.query(SchoolSession).filter_by(
        id=scope["session_id"], school_id=scope["school_id"],
    ).first()
    if session is None:
        raise HTTPException(404, "School session not found")
    start_date = session.start_date.replace(day=1) if by_month else session.start_date
    if not start_date <= selected_date <= session.end_date:
        raise HTTPException(422, "Monthly adjustment date must be within the academic session")
    return session.end_date


def monthly_dates(selected_date, end_date):
    for session_month in session_months(selected_date, end_date):
        year, month = map(int, session_month.split("-"))
        day = min(selected_date.day, monthrange(year, month)[1])
        yield min(date(year, month, day), end_date)


def prepare_adjustments(db, scope, payload):
    is_discount = payload.type == FeeAdjustmentType.DISCOUNT
    is_monthly = payload.frequency == FeeAdjustmentFrequency.MONTHLY
    dates = [payload.date]
    if is_monthly:
        end_date = monthly_session_end(db, scope, payload.date, by_month=is_discount)
        dates = list(monthly_dates(payload.date, end_date))
    skipped_months = []
    if is_discount:
        failures = discount_due_failures(db, scope, dates, payload.amount)
        if failures and not is_monthly:
            raise HTTPException(422, failures[0])
        skipped_months = failures
        months_to_skip = {failure["fee_month"] for failure in skipped_months}
        dates = [selected for selected in dates if selected.strftime("%Y-%m") not in months_to_skip]
        if not dates:
            raise HTTPException(422, {
                "message": "No month has enough remaining due for this discount",
                "skipped_months": skipped_months,
            })
    fields = payload.model_dump(exclude={"date"})
    if is_discount:
        fields["applied_at"] = datetime.now(timezone.utc)
    items = [StudentFeeAdjustment(**scope, **fields, date=adjustment_date) for adjustment_date in dates]
    return items, skipped_months


@router.post("", response_model=StudentFeeAdjustmentCreateResult, status_code=201)
def create_adjustment(
    payload: StudentFeeAdjustmentWrite,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    lock_adjustment_student(db, scope)
    items, skipped_months = prepare_adjustments(db, scope, payload)
    save_adjustments(db, items)
    db.refresh(items[0])
    message = (
        "Discount applied successfully"
        if payload.type == FeeAdjustmentType.DISCOUNT else f"Created {len(items)} student fee adjustment(s)"
    )
    return {"message": message, "data": items[0], "skipped_months": skipped_months}


@router.post("/bulk-upload", response_model=StudentFeeAdjustmentBulkResult, status_code=201)
def bulk_create_adjustments(
    payload: Annotated[list[StudentFeeAdjustmentWrite], Body(min_length=1, max_length=1000)],
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    lock_adjustment_student(db, scope)
    results = []
    total = 0
    for row, adjustment in enumerate(payload, start=1):
        try:
            items, skipped_months = prepare_adjustments(db, scope, adjustment)
            db.add_all(items)
            # Make earlier rows visible to the next discount balance check.
            db.flush()
            results.append({
                "row": row,
                "data": [StudentFeeAdjustmentResponse.model_validate(item) for item in items],
                "skipped_months": skipped_months,
            })
            total += len(items)
        except HTTPException as exc:
            db.rollback()
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": exc.detail}
            raise HTTPException(exc.status_code, {"row": row, **detail}) from exc
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, {"row": row, "message": "Fee adjustment conflicts with database constraints"}) from exc

    commit_adjustments(db)
    return {"message": "Student fee adjustments saved successfully", "total": total, "data": results}


@router.get("", response_model=StudentFeeAdjustmentListResult)
def list_adjustments(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    return paginate_adjustments(db.query(StudentFeeAdjustment).filter_by(**scope), offset, limit)


def paginate_adjustments(query, offset, limit):
    total = query.count()
    items = (
        query.order_by(StudentFeeAdjustment.date.asc(), StudentFeeAdjustment.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "message": "Student fee adjustments fetched successfully",
        "total": total,
        "offset": offset,
        "limit": limit,
        "data": items,
    }


@router.get("/students", response_model=FeeAdjustmentStudentListResult)
def list_adjustment_students(
    school_id: int,
    session_id: int,
    student_id: int | None = Query(None, gt=0),
    class_id: PositiveInt | AllFilter | None = None,
    section_id: PositiveInt | AllFilter | None = None,
    adjustment_type: FeeAdjustmentType | AllFilter | None = Query(None, alias="type"),
    status: Literal["active", "inactive", "completed", "promoted"] | None = None,
    search: str | None = Query(None, max_length=767),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(fee_structure_school),
):
    query = db.query(Student).filter_by(school_id=school_id, session_id=session_id)
    for column, value in (
        (Student.id, student_id), (Student.class_id, class_id), (Student.section_id, section_id),
        (Student.status, status),
    ):
        if value not in (None, "all", "All", "ALL"):
            query = query.filter(column == value)
    adjustments = db.query(StudentFeeAdjustment).filter_by(school_id=school_id, session_id=session_id)
    if isinstance(adjustment_type, FeeAdjustmentType):
        matching = adjustments.filter_by(type=adjustment_type.value)
        query = query.filter(matching.filter(StudentFeeAdjustment.student_id == Student.id).exists())
    term = (search or "").strip()
    if term:
        name_match = and_(*(or_(
            Student.first_name.icontains(part, autoescape=True),
            Student.middle_name.icontains(part, autoescape=True),
            Student.last_name.icontains(part, autoescape=True),
        ) for part in term.split()))
        query = query.filter(or_(name_match, Student.admission_number.icontains(term, autoescape=True)))
    total = query.count()
    rows = (
        query.with_entities(
            Student.id, Student.first_name, Student.middle_name, Student.last_name, Student.admission_number,
            Student.class_id, SchoolClass.name.label("class_name"),
            Student.section_id, Section.name.label("section_name"), Student.status,
        )
        .outerjoin(SchoolClass, and_(SchoolClass.id == Student.class_id, SchoolClass.school_id == school_id))
        .outerjoin(Section, and_(Section.id == Student.section_id, Section.school_id == school_id))
        .order_by(
            SchoolClass.class_order.asc().nulls_last(), func.lower(SchoolClass.name), SchoolClass.id,
            func.lower(Section.name).asc().nulls_last(), Section.id,
            func.lower(Student.first_name), func.lower(func.coalesce(Student.middle_name, "")),
            func.lower(func.coalesce(Student.last_name, "")), Student.id,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    totals = {}
    if rows:
        page_totals = (
            adjustments.filter(StudentFeeAdjustment.student_id.in_([row.id for row in rows]))
            .with_entities(
                StudentFeeAdjustment.student_id, StudentFeeAdjustment.type,
                func.count(), func.sum(StudentFeeAdjustment.amount),
            )
            .group_by(StudentFeeAdjustment.student_id, StudentFeeAdjustment.type)
        )
        totals = {(student, kind): (count, amount) for student, kind, count, amount in page_totals}

    data = []
    for row in rows:
        discount_count, discount_amount = totals.get((row.id, FeeAdjustmentType.DISCOUNT.value), (0, Decimal("0.00")))
        penalty_count, penalty_amount = totals.get((row.id, FeeAdjustmentType.PENALTY.value), (0, Decimal("0.00")))
        data.append({
            "student_id": row.id,
            "student_name": " ".join(part for part in (row.first_name, row.middle_name, row.last_name) if part),
            "admission_number": row.admission_number,
            "class_id": row.class_id,
            "class_name": row.class_name,
            "section_id": row.section_id,
            "section_name": row.section_name,
            "status": row.status,
            "total_discount_amount": discount_amount,
            "total_penalty_amount": penalty_amount,
            "discount": {"count": discount_count, "total_amount": discount_amount},
            "penalty": {"count": penalty_count, "total_amount": penalty_amount},
        })
    return {
        "message": "Students fetched successfully",
        "total": total,
        "offset": offset,
        "limit": limit,
        "data": data,
    }


@router.get("/students/adjustments", response_model=StudentFeeAdjustmentListResult)
def list_student_adjustments_by_type(
    adjustment_type: FeeAdjustmentType | AllFilter = Query(alias="type"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    query = db.query(StudentFeeAdjustment).filter_by(**scope)
    if isinstance(adjustment_type, FeeAdjustmentType):
        query = query.filter_by(type=adjustment_type.value)
    return paginate_adjustments(query, offset, limit)


@router.get("/{adjustment_id}", response_model=StudentFeeAdjustmentResult)
def get_adjustment(
    adjustment_id: int,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    return {
        "message": "Student fee adjustment fetched successfully",
        "data": find_adjustment(db, scope, adjustment_id),
    }


@router.put("/{adjustment_id}", response_model=StudentFeeAdjustmentResult)
def update_adjustment(
    adjustment_id: int,
    payload: StudentFeeAdjustmentWrite,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    lock_adjustment_student(db, scope)
    item = find_adjustment(db, scope, adjustment_id, lock=True)
    require_editable_adjustment(db, scope, item)
    is_discount = payload.type == FeeAdjustmentType.DISCOUNT
    if payload.frequency == FeeAdjustmentFrequency.MONTHLY:
        monthly_session_end(db, scope, payload.date, by_month=is_discount)
    if is_discount:
        validate_discount_due(db, scope, [payload.date], payload.amount, exclude_adjustment_id=item.id)
    fields = payload.model_dump()
    if "description" not in payload.model_fields_set:
        fields.pop("description")
    for field, value in fields.items():
        setattr(item, field, value)
    if is_discount and item.applied_at is None:
        item.applied_at = datetime.now(timezone.utc)
    save_adjustments(db, [item])
    db.refresh(item)
    message = (
        "Discount applied successfully"
        if is_discount else "Student fee adjustment updated successfully"
    )
    return {"message": message, "data": item}


@router.delete("/{adjustment_id}", status_code=204)
def delete_adjustment(
    adjustment_id: int,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    lock_adjustment_student(db, scope)
    item = find_adjustment(db, scope, adjustment_id, lock=True)
    require_editable_adjustment(db, scope, item)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.post("/{adjustment_id}/mark_applied", response_model=StudentFeeAdjustmentResult)
def mark_adjustment_applied(
    adjustment_id: int,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    lock_adjustment_student(db, scope)
    item = find_adjustment(db, scope, adjustment_id, lock=True)
    if item.applied_at is None:
        if item.type == FeeAdjustmentType.DISCOUNT:
            validate_discount_due(db, scope, [item.date], item.amount, exclude_adjustment_id=item.id)
        item.applied_at = datetime.now(timezone.utc)
    save_adjustments(db, [item])
    db.refresh(item)
    message = (
        "Discount applied successfully"
        if item.type == FeeAdjustmentType.DISCOUNT else "Student fee adjustment marked as applied"
    )
    return {"message": message, "data": item}

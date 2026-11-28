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
from models.student_promotion import StudentPromotion
from models.student_fee_adjustment import StudentFeeAdjustment
from routers.fee_assignments import fee_payment_months
from routers.fee_structures import fee_structure_school
from schemas.enums import FeeAdjustmentFrequency, FeeAdjustmentStatus, FeeAdjustmentType, FeeDiscountType, StudentStatus
from schemas.student_fee_adjustment import (
    FeeAdjustmentDropdownResult,
    FeeAdjustmentStudentListResult,
    StudentFeeAdjustmentBulkResult,
    StudentFeeAdjustmentCreateResult,
    StudentFeeAdjustmentListResult,
    StudentFeeAdjustmentMonthBulkResult,
    StudentFeeAdjustmentMonthBulkWrite,
    StudentFeeAdjustmentResponse,
    StudentFeeAdjustmentResult,
    StudentFeeAdjustmentWrite,
)
from utils.fee_months import session_months

router = APIRouter(prefix="/student_fee_adjustments", tags=["Configure Fees"])
AllFilter = Literal["all", "All", "ALL"]
BULK_UPLOAD_TAG = "Bulk Upload"


@router.get("/dropdowns", response_model=FeeAdjustmentDropdownResult)
def get_fee_adjustment_dropdowns():
    return {
        "message": "Fee adjustment dropdowns fetched successfully",
        "data": {
            "types": list(FeeAdjustmentType),
            "frequencies": list(FeeAdjustmentFrequency),
            "discount_types": list(FeeDiscountType),
            "statuses": list(FeeAdjustmentStatus),
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


def require_editable_adjustment(item):
    if not item.editable:
        raise HTTPException(409, "This adjustment is locked because a payment or adjustment has already been applied")


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
    student = db.query(Student.id, Student.admission_number).filter_by(
        id=scope["student_id"], school_id=scope["school_id"], session_id=scope["session_id"],
    ).with_for_update().first()
    if student is None:
        raise HTTPException(404, "Student not found for this school session")
    require_untransferred_balance(db, student.id)
    return student.admission_number


def require_untransferred_balance(db, student_id):
    if db.get(StudentPromotion, student_id) is not None:
        raise HTTPException(409, "This balance was transferred to a later session")


def discount_due_failures(db, scope, dates, amount, *, fee_head_id=None, exclude_adjustment_id=None):
    months = [selected.strftime("%Y-%m") for selected in dates]
    balances = fee_payment_months(
        db, **scope, months=months, exclude_adjustment_id=exclude_adjustment_id, include_pending_discounts=True,
    )
    failures = []
    for balance in balances:
        due = sum((head.principal_due for head in balance.fee_heads), Decimal("0.00"))
        due -= sum(
            line.effective_discount_amount for line in balance.fee_adjustment
            if line.tag == "discount" and line.fee_head_id is None
        )
        if fee_head_id is not None:
            if any(head.fee_head_id == fee_head_id for head in balance.fee_heads):
                continue
            due = Decimal("0.00")
            message = "Fee head is not assigned for this month"
        elif due <= 0:
            message = "All fees are paid for this month"
        elif amount > due:
            message = "Discount amount cannot exceed the remaining due"
        else:
            continue
        failures.append({
            "message": message,
            "fee_month": balance.fee_month,
            "due_amount": str(due),
            "discount_amount": str(amount),
        })
    return failures


def validate_discount_due(db, scope, dates, amount, *, fee_head_id=None, exclude_adjustment_id=None):
    failures = discount_due_failures(
        db, scope, dates, amount, fee_head_id=fee_head_id, exclude_adjustment_id=exclude_adjustment_id,
    )
    if failures:
        raise HTTPException(422, failures[0])


def validate_penalty_head(db, scope, dates, fee_head_id):
    if fee_head_id is None:
        return
    months = [selected.strftime("%Y-%m") for selected in dates]
    for balance in fee_payment_months(db, **scope, months=months):
        if all(head.fee_head_id != fee_head_id for head in balance.fee_heads):
            raise HTTPException(422, {
                "message": "Fee head is not assigned for this month",
                "fee_month": balance.fee_month,
                "fee_head_id": fee_head_id,
            })


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


def prepare_adjustments(db, scope, payload, *, expand_monthly=True):
    is_discount = payload.type == FeeAdjustmentType.DISCOUNT
    is_monthly = payload.frequency == FeeAdjustmentFrequency.MONTHLY
    dates = [payload.date]
    if is_monthly and expand_monthly:
        end_date = monthly_session_end(db, scope, payload.date, by_month=is_discount)
        dates = list(monthly_dates(payload.date, end_date))
    skipped_months = []
    if is_discount:
        failures = discount_due_failures(db, scope, dates, payload.amount, fee_head_id=payload.fee_head_id)
        if failures and (not is_monthly or not expand_monthly):
            raise HTTPException(422, failures[0])
        skipped_months = failures
        months_to_skip = {failure["fee_month"] for failure in skipped_months}
        dates = [selected for selected in dates if selected.strftime("%Y-%m") not in months_to_skip]
        if not dates:
            reject_all_skipped(skipped_months)
    else:
        validate_penalty_head(db, scope, dates, payload.fee_head_id)
    fields = payload.model_dump(exclude={"date", "status"})
    items = [StudentFeeAdjustment(**scope, **fields, date=adjustment_date) for adjustment_date in dates]
    return items, skipped_months


def reject_all_skipped(skipped_months):
    raise HTTPException(422, {
        "message": "No month has enough remaining due for this discount",
        "skipped_months": skipped_months,
    })


@router.post("", response_model=StudentFeeAdjustmentCreateResult, status_code=201)
def create_adjustment(
    payload: StudentFeeAdjustmentWrite,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    """Create a student adjustment. Use fee_head_id to target an assigned fee head."""
    admission_number = lock_adjustment_student(db, scope)
    items, skipped_months = prepare_adjustments(db, scope, payload)
    for item in items:
        item.admission_number = admission_number
    save_adjustments(db, items)
    db.refresh(items[0])
    message = (
        "Discount assigned successfully"
        if payload.type == FeeAdjustmentType.DISCOUNT
        else f"Created {len(items)} student fee adjustment(s)"
    )
    return {"message": message, "data": items[0], "skipped_months": skipped_months}


@router.post("/bulk-upload", response_model=StudentFeeAdjustmentBulkResult, status_code=201)
def bulk_create_adjustments(
    payload: Annotated[list[StudentFeeAdjustmentWrite], Body(min_length=1, max_length=1000)],
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    """Upload discount or penalty rows for one student. Each row can target an assigned fee head."""
    admission_number = lock_adjustment_student(db, scope)
    return save_bulk_adjustments(db, (
        (scope, admission_number, [adjustment]) for adjustment in payload
    ))


def save_bulk_adjustments(db, rows, *, expand_monthly=True):
    results = []
    total = 0
    for row, (scope, admission_number, adjustments) in enumerate(rows, start=1):
        try:
            items, skipped_months = [], []
            for adjustment in adjustments:
                prepared, skipped = prepare_adjustments(db, scope, adjustment, expand_monthly=expand_monthly)
                for item in prepared:
                    item.tag = BULK_UPLOAD_TAG
                    item.admission_number = admission_number
                db.add_all(prepared)
                # Later entries can use the same date and must see earlier adjustments.
                db.flush()
                items.extend(prepared)
                skipped_months.extend(skipped)
            results.append({
                "row": row,
                "status": items[0].status,
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


@router.post("/bulk-upload/monthly", response_model=StudentFeeAdjustmentMonthBulkResult, status_code=201)
def bulk_create_month_adjustments(
    payload: Annotated[
        list[StudentFeeAdjustmentMonthBulkWrite],
        Body(
            min_length=1,
            max_length=1000,
            openapi_examples={
                "fee_head_discount": {
                    "summary": "Pending discount for an assigned fee head",
                    "value": [{
                        "admission_number": "004003",
                        "type": "Discount",
                        "frequency": "Monthly",
                        "discount_type": "Charity",
                        "fee_head_id": 12,
                        "status": "Pending",
                        "month": [{"amount": 250, "date": "2026-11"}],
                    }],
                },
                "fee_head_penalty": {
                    "summary": "Penalty for an assigned fee head",
                    "value": [{
                        "admission_number": "004003",
                        "type": "Penalty",
                        "frequency": "One Time",
                        "fee_head_id": 12,
                        "status": "Pending",
                        "month": [{"amount": 100, "date": "2026-11"}],
                    }],
                },
                "one_time_discount": {
                    "summary": "Two one-time discounts on the same date",
                    "value": [
                        {
                            "admission_number": "004003",
                            "type": "Discount",
                            "frequency": "One Time",
                            "discount_type": "Charity",
                            "month": [
                                {"amount": 6291, "date": "2026-11-20", "description": ""},
                                {"amount": 1791, "date": "2026-11-20", "description": ""},
                            ],
                        }
                    ],
                }
            },
        ),
    ],
    school_id: int,
    session_id: int,
    db: Session = Depends(fee_structure_school),
):
    """Upload discounts or penalties by admission number. Use one row per student and fee head."""
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).one()
    first_month = session.start_date.replace(day=1)
    rows = []
    for row, adjustment in enumerate(payload, start=1):
        if adjustment.frequency == FeeAdjustmentFrequency.MONTHLY:
            seen = set()
            for entry in adjustment.month:
                if not first_month <= entry.date <= session.end_date:
                    raise HTTPException(422, {"row": row, "message": f"{entry.date} must be within the selected session"})
                month = entry.date.replace(day=1)
                if month in seen:
                    raise HTTPException(422, {"row": row, "message": f"{month:%Y-%m} appears more than once in month"})
                seen.add(month)
        fields = adjustment.model_dump(exclude={"admission_number", "month"})
        entries = [StudentFeeAdjustmentWrite(**fields, **entry.model_dump()) for entry in adjustment.month]
        rows.append((adjustment.admission_number, entries))

    admission_numbers = {admission_number for admission_number, _ in rows}
    student_ids = dict(
        db.query(Student.admission_number, Student.id)
        .filter_by(school_id=school_id, session_id=session_id)
        .filter(Student.admission_number.in_(admission_numbers))
        .order_by(Student.id)
        .with_for_update()
        .all()
    )
    scoped_rows = []
    for row, (admission_number, entries) in enumerate(rows, start=1):
        student_id = student_ids.get(admission_number)
        if student_id is None:
            raise HTTPException(404, {
                "row": row,
                "admission_number": admission_number,
                "message": "Admission number not found in this school session",
            })
        scope = {"school_id": school_id, "session_id": session_id, "student_id": student_id}
        require_untransferred_balance(db, student_id)
        scoped_rows.append((scope, admission_number, entries))
    result = save_bulk_adjustments(db, scoped_rows, expand_monthly=False)
    for saved_row, adjustment in zip(result["data"], payload, strict=True):
        saved_row["admission_number"] = adjustment.admission_number
    return result


@router.get("", response_model=StudentFeeAdjustmentListResult)
def list_adjustments(
    status: FeeAdjustmentStatus | None = None,
    fee_head_id: PositiveInt | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    return paginate_adjustments(db.query(StudentFeeAdjustment).filter_by(**scope), offset, limit, status, fee_head_id)


def paginate_adjustments(query, offset, limit, status, fee_head_id):
    if fee_head_id is not None:
        query = query.filter(StudentFeeAdjustment.fee_head_id == fee_head_id)
    if status is not None:
        query = query.filter(StudentFeeAdjustment.status == status)
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
    status: StudentStatus | None = None,
    search: str | None = Query(None, max_length=767),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(fee_structure_school),
):
    query = db.query(Student).filter_by(school_id=school_id, session_id=session_id)
    for column, value in (
        (Student.id, student_id), (Student.class_id, class_id), (Student.section_id, section_id),
        (Student.status, status.value if status is not None else None),
    ):
        if value not in (None, "all", "All", "ALL"):
            query = query.filter(column == value)
    adjustments = db.query(StudentFeeAdjustment).filter_by(school_id=school_id, session_id=session_id)
    matching = adjustments.with_entities(StudentFeeAdjustment.student_id)
    if isinstance(adjustment_type, FeeAdjustmentType):
        matching = matching.filter_by(type=adjustment_type.value)
    query = query.filter(Student.id.in_(
        matching.group_by(StudentFeeAdjustment.student_id).having(func.sum(StudentFeeAdjustment.amount) > 0)
    ))
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
    status: FeeAdjustmentStatus | None = None,
    fee_head_id: PositiveInt | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    query = db.query(StudentFeeAdjustment).filter_by(**scope)
    if isinstance(adjustment_type, FeeAdjustmentType):
        query = query.filter_by(type=adjustment_type.value)
    return paginate_adjustments(query, offset, limit, status, fee_head_id)


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
    require_editable_adjustment(item)
    is_discount = payload.type == FeeAdjustmentType.DISCOUNT
    if payload.frequency == FeeAdjustmentFrequency.MONTHLY:
        monthly_session_end(db, scope, payload.date, by_month=is_discount or item.tag == BULK_UPLOAD_TAG)
    fee_head_id = payload.fee_head_id
    if "fee_head_id" not in payload.model_fields_set:
        fee_head_id = item.fee_head_id
    if is_discount:
        validate_discount_due(
            db, scope, [payload.date], payload.amount, fee_head_id=fee_head_id, exclude_adjustment_id=item.id,
        )
    else:
        validate_penalty_head(db, scope, [payload.date], fee_head_id)
    keep_applied = is_discount and item.type == payload.type and item.applied_at is not None
    fields = payload.model_dump(exclude={"status"})
    fields["fee_head_id"] = fee_head_id
    if "description" not in payload.model_fields_set:
        fields.pop("description")
    for field, value in fields.items():
        setattr(item, field, value)
    if not keep_applied:
        item.applied_at = None
    save_adjustments(db, [item])
    db.refresh(item)
    message = (
        "Discount applied successfully"
        if keep_applied else "Student fee adjustment updated successfully"
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
    require_editable_adjustment(item)
    if item.type == FeeAdjustmentType.DISCOUNT and item.fee_head_id is None and item.applied_at is not None:
        validate_discount_due(db, scope, [item.date], item.amount, exclude_adjustment_id=item.id)
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
            validate_discount_due(
                db, scope, [item.date], item.amount, fee_head_id=item.fee_head_id, exclude_adjustment_id=item.id,
            )
        item.applied_at = datetime.now(timezone.utc)
    save_adjustments(db, [item])
    db.refresh(item)
    message = (
        "Discount applied successfully"
        if item.type == FeeAdjustmentType.DISCOUNT else "Student fee adjustment marked as applied"
    )
    return {"message": message, "data": item}

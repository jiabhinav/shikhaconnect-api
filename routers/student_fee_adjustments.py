from calendar import monthrange
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.session import Session as SchoolSession
from models.student import Student
from models.student_fee_adjustment import StudentFeeAdjustment
from routers.fee_structures import fee_structure_school
from schemas.enums import FeeAdjustmentFrequency, FeeAdjustmentType, FeeDiscountType
from schemas.student_fee_adjustment import (
    FeeAdjustmentDropdownResult,
    StudentFeeAdjustmentListResult,
    StudentFeeAdjustmentResult,
    StudentFeeAdjustmentWrite,
)
from utils.fee_months import session_months

router = APIRouter(prefix="/student_fee_adjustments", tags=["Configure Fees"])


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


def require_unapplied(item):
    if item.applied_at is not None:
        raise HTTPException(409, "Applied student fee adjustments cannot be edited or deleted")


def save_adjustments(db, items):
    db.add_all(items)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Student fee adjustment conflicts with database constraints") from exc


def monthly_session_end(db, scope, selected_date):
    session = db.query(SchoolSession).filter_by(
        id=scope["session_id"], school_id=scope["school_id"],
    ).first()
    if session is None:
        raise HTTPException(404, "School session not found")
    if not session.start_date <= selected_date <= session.end_date:
        raise HTTPException(422, "Monthly adjustment date must be within the academic session")
    return session.end_date


def monthly_dates(selected_date, end_date):
    for session_month in session_months(selected_date, end_date):
        year, month = map(int, session_month.split("-"))
        day = min(selected_date.day, monthrange(year, month)[1])
        yield min(date(year, month, day), end_date)


@router.post("", response_model=StudentFeeAdjustmentResult, status_code=201)
def create_adjustment(
    payload: StudentFeeAdjustmentWrite,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    dates = [payload.date]
    if payload.frequency == FeeAdjustmentFrequency.MONTHLY:
        end_date = monthly_session_end(db, scope, payload.date)
        dates = monthly_dates(payload.date, end_date)
    fields = payload.model_dump(exclude={"date"})
    items = [StudentFeeAdjustment(**scope, **fields, date=adjustment_date) for adjustment_date in dates]
    save_adjustments(db, items)
    db.refresh(items[0])
    message = f"Created {len(items)} student fee adjustment(s)"
    return {"message": message, "data": items[0]}


@router.get("", response_model=StudentFeeAdjustmentListResult)
def list_adjustments(scope: dict = Depends(adjustment_scope), db: Session = Depends(fee_structure_school)):
    items = (
        db.query(StudentFeeAdjustment)
        .filter_by(**scope)
        .order_by(StudentFeeAdjustment.date.desc(), StudentFeeAdjustment.id.desc())
        .all()
    )
    return {"message": "Student fee adjustments fetched successfully", "total": len(items), "data": items}


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
    item = find_adjustment(db, scope, adjustment_id, lock=True)
    require_unapplied(item)
    if payload.frequency == FeeAdjustmentFrequency.MONTHLY:
        monthly_session_end(db, scope, payload.date)
    for field, value in payload.model_dump().items():
        setattr(item, field, value)
    save_adjustments(db, [item])
    db.refresh(item)
    return {"message": "Student fee adjustment updated successfully", "data": item}


@router.delete("/{adjustment_id}", status_code=204)
def delete_adjustment(
    adjustment_id: int,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    item = find_adjustment(db, scope, adjustment_id, lock=True)
    require_unapplied(item)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.post("/{adjustment_id}/mark_applied", response_model=StudentFeeAdjustmentResult)
def mark_adjustment_applied(
    adjustment_id: int,
    scope: dict = Depends(adjustment_scope),
    db: Session = Depends(fee_structure_school),
):
    item = find_adjustment(db, scope, adjustment_id, lock=True)
    if item.applied_at is None:
        item.applied_at = datetime.now(timezone.utc)
    save_adjustments(db, [item])
    db.refresh(item)
    return {"message": "Student fee adjustment marked as applied", "data": item}

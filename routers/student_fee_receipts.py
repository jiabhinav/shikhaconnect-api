import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from models.student import Student
from models.student_fee import StudentFee
from routers.fee_structures import fee_structure_school
from schemas.student_fee import StudentFeeReceiptListResult
from utils.dates import payment_timezone


router = APIRouter(tags=["Configure Fees"])


def payment_day_start(day):
    return datetime.combine(day, time.min, tzinfo=payment_timezone()).astimezone(timezone.utc)


@router.get("/student_fees/receipts", response_model=StudentFeeReceiptListResult)
def list_student_fee_receipts(
    school_id: int,
    session_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    class_id: int | None = Query(None, gt=0),
    section_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, max_length=250),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(fee_structure_school),
):
    """Return monthly student_fees rows that have a receipt number."""
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(422, "end_date must be on or after start_date")

    query = db.query(*StudentFee.__table__.columns).filter(
        StudentFee.school_id == school_id,
        StudentFee.academic_session_id == session_id,
        StudentFee.receipt_number.is_not(None),
    )
    if start_date is not None or end_date is not None:
        query = query.filter(StudentFee.payment_date.is_not(None))
    if start_date is not None and start_date > date.min:
        query = query.filter(StudentFee.payment_date >= payment_day_start(start_date))
    if end_date is not None and end_date < date.max:
        query = query.filter(StudentFee.payment_date < payment_day_start(end_date + timedelta(days=1)))

    term = (search or "").strip()
    if term or class_id is not None or section_id is not None:
        query = query.join(Student, and_(Student.id == StudentFee.student_id, Student.school_id == school_id))
    if class_id is not None:
        query = query.filter(Student.class_id == class_id)
    if section_id is not None:
        query = query.filter(Student.section_id == section_id)
    if term:
        name_match = and_(*(or_(
            Student.first_name.icontains(part, autoescape=True),
            Student.middle_name.icontains(part, autoescape=True),
            Student.last_name.icontains(part, autoescape=True),
        ) for part in term.split()))
        conditions = [
            name_match,
            Student.admission_number.icontains(term, autoescape=True),
            StudentFee.receipt_number.icontains(term, autoescape=True),
        ]
        amount = term.removeprefix("₹").replace(",", "").strip()
        if re.fullmatch(r"\d{1,16}(?:\.\d{1,2})?", amount):
            conditions.append(StudentFee.paid_amount == Decimal(amount))
        query = query.filter(or_(*conditions))

    total = query.count()
    undated = StudentFee.payment_date.is_(None)
    prefix = func.rtrim(StudentFee.receipt_number, "0123456789")
    suffix = func.ltrim(func.substr(StudentFee.receipt_number, func.length(prefix) + 1), "0")
    records = query.order_by(
        StudentFee.payment_date.desc().nulls_last(),
        case((undated, prefix)).desc(),
        case((undated, func.length(suffix))).desc(),
        case((undated, suffix)).desc(),
        StudentFee.id.desc(),
    ).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit, "data": records}

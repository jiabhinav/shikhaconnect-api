from io import BytesIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import ValidationError
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from models.class_section import SchoolClass, Section
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.session import Session as SchoolSession
from models.student import Student
from models.student_carry_forward import StudentCarryForward
from models.user import User, UserRole
from routers.fee_structures import fee_structure_school
from schemas.enums import StudentFeeStatus
from schemas.student_carry_forward import (
    CarryForwardAmountWrite,
    CarryForwardBulkResult,
    CarryForwardBulkWrite,
    CarryForwardEditResult,
    CarryForwardListResult,
    CarryForwardRawBulkWrite,
    CarryForwardUploadRow,
)

router = APIRouter(tags=["Configure Fees"])
MAX_EXCEL_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_EXCEL_BYTES = 50 * 1024 * 1024
MAX_EXCEL_ROWS = 1000


def carry_forward_db(
    school_id: int,
    db: Session = Depends(fee_structure_school),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN) or db.query(SchoolMapping.id).filter_by(
            school_id=school_id,
            user_id=current_user.login_user_id,
            status=SchoolMappingStatus.ACTIVE,
        ).first() is None:
            raise HTTPException(403, "You cannot access carry-forward fees for this school")
    return db


@router.get("/student_carry_forwards", response_model=CarryForwardListResult)
def list_carry_forwards(
    school_id: int,
    session_id: int,
    class_id: int | None = Query(None, gt=0),
    section_id: int | None = Query(None, gt=0),
    payment_status: StudentFeeStatus | None = None,
    search: str | None = Query(None, max_length=767),
    db: Session = Depends(carry_forward_db),
):
    status = case(
        (StudentCarryForward.unpaid_amount == 0, StudentFeeStatus.PAID.value),
        (StudentCarryForward.paid_amount == 0, StudentFeeStatus.UNPAID.value),
        else_=StudentFeeStatus.PARTIAL.value,
    )
    scope = (Student.school_id == school_id, Student.session_id == session_id)
    query = (
        db.query(
            StudentCarryForward.id.label("carry_forward_id"), Student,
            SchoolClass.name.label("class_name"), Section.name.label("section_name"),
            StudentCarryForward.amount, StudentCarryForward.paid_amount,
            StudentCarryForward.unpaid_amount.label("due_amount"),
            status.label("payment_status"),
        )
        .select_from(Student)
        .join(StudentCarryForward, and_(
            StudentCarryForward.student_id == Student.id,
            StudentCarryForward.school_id == Student.school_id,
            StudentCarryForward.session_id == Student.session_id,
        ))
        .outerjoin(SchoolClass, and_(SchoolClass.id == Student.class_id, SchoolClass.school_id == school_id))
        .outerjoin(Section, and_(Section.id == Student.section_id, Section.school_id == school_id))
        .filter(*scope, StudentCarryForward.amount > 0)
    )
    filters = []
    if class_id is not None:
        filters.append(Student.class_id == class_id)
    if section_id is not None:
        filters.append(Student.section_id == section_id)
    if payment_status is not None:
        filters.append(status == payment_status.value)
    for part in (search or "").split():
        filters.append(or_(
            Student.first_name.icontains(part, autoescape=True),
            Student.middle_name.icontains(part, autoescape=True),
            Student.last_name.icontains(part, autoescape=True),
        ))

    rows = query.filter(*filters).order_by(
        SchoolClass.class_order.asc().nulls_last(),
        func.lower(SchoolClass.name),
        SchoolClass.id,
        func.lower(Section.name).asc().nulls_last(),
        Section.id,
        func.lower(Student.first_name),
        func.lower(func.coalesce(Student.middle_name, "")),
        func.lower(func.coalesce(Student.last_name, "")),
        Student.id,
    ).all()
    data = []
    for serial_number, row in enumerate(rows, start=1):
        student = row.Student
        name = " ".join(part for part in (student.first_name, student.middle_name, student.last_name) if part)
        data.append({
            "serial_number": serial_number,
            "id": row.carry_forward_id,
            "student_id": student.id,
            "student_name": name,
            "admission_number": student.admission_number,
            "class_id": student.class_id,
            "class_name": row.class_name,
            "section_id": student.section_id,
            "section_name": row.section_name,
            "amount": row.amount,
            "paid_amount": row.paid_amount,
            "due_amount": row.due_amount,
            "status": row.payment_status,
        })
    total_records = query.with_entities(func.count(StudentCarryForward.id)).scalar() if filters else len(data)
    return {
        "message": "Carry-forward fees fetched successfully",
        "total": len(data),
        "total_records": total_records,
        "data": data,
    }


def get_carry_forward_for_update(db: Session, school_id: int, session_id: int, carry_forward_id: int):
    result = (
        db.query(StudentCarryForward, Student.admission_number)
        .join(Student, and_(
            Student.id == StudentCarryForward.student_id,
            Student.school_id == StudentCarryForward.school_id,
            Student.session_id == StudentCarryForward.session_id,
        ))
        .filter(
            StudentCarryForward.id == carry_forward_id,
            StudentCarryForward.school_id == school_id,
            StudentCarryForward.session_id == session_id,
            StudentCarryForward.amount > 0,
        )
        .with_for_update(of=StudentCarryForward)
        .one_or_none()
    )
    if result is None:
        raise HTTPException(404, "Carry-forward fee not found")
    item, _ = result
    if item.paid_amount > 0:
        raise HTTPException(409, "Only unpaid carry-forward fees can be edited or deleted")
    return result


@router.put("/student_carry_forwards/{carry_forward_id}", response_model=CarryForwardEditResult)
def edit_carry_forward(
    carry_forward_id: int,
    school_id: int,
    session_id: int,
    payload: CarryForwardAmountWrite,
    db: Session = Depends(carry_forward_db),
):
    item, admission_number = get_carry_forward_for_update(db, school_id, session_id, carry_forward_id)
    item.amount = payload.amount
    db.flush()
    result = {
        "message": "Carry-forward fee updated successfully",
        "data": {
            "id": item.id,
            "student_id": item.student_id,
            "admission_number": admission_number,
            "amount": item.amount,
            "paid_amount": item.paid_amount,
            "unpaid_amount": item.unpaid_amount,
        },
    }
    db.commit()
    return result


@router.delete("/student_carry_forwards/{carry_forward_id}", status_code=204)
def delete_carry_forward(
    carry_forward_id: int,
    school_id: int,
    session_id: int,
    db: Session = Depends(carry_forward_db),
):
    item, _ = get_carry_forward_for_update(db, school_id, session_id, carry_forward_id)
    # Keep the per-student record used by student creation and bulk upload.
    item.amount = 0
    db.commit()


@router.post("/student_carry_forwards/bulk-upload/json", response_model=CarryForwardBulkResult)
def bulk_upload_carry_forwards(
    school_id: int,
    session_id: int,
    payload: CarryForwardBulkWrite,
    db: Session = Depends(carry_forward_db),
):
    return save_carry_forwards(db, school_id, session_id, payload.rows)


@router.post("/student_carry_forwards/bulk-upload/raw", response_model=CarryForwardBulkResult)
def bulk_upload_carry_forwards_raw(
    school_id: int,
    session_id: int,
    payload: CarryForwardRawBulkWrite,
    db: Session = Depends(carry_forward_db),
):
    return save_carry_forwards(db, school_id, session_id, payload.root)


@router.post("/student_carry_forwards/bulk-upload", response_model=CarryForwardBulkResult)
@router.post(
    "/student_carry_forwards/bulk-upload/excel",
    response_model=CarryForwardBulkResult,
    include_in_schema=False,
)
def bulk_upload_carry_forwards_excel(
    school_id: int,
    session_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(carry_forward_db),
):
    payload = read_carry_forward_workbook(file)
    return save_carry_forwards(db, school_id, session_id, payload.rows)


def read_carry_forward_workbook(file: UploadFile) -> CarryForwardBulkWrite:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(422, "Upload an Excel .xlsx file")
    content = file.file.read(MAX_EXCEL_BYTES + 1)
    if len(content) > MAX_EXCEL_BYTES:
        raise HTTPException(413, "Excel file exceeds 5 MB")

    workbook = None
    rows = []
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_EXPANDED_EXCEL_BYTES:
                raise HTTPException(413, "Expanded Excel file exceeds 50 MB")
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()
        records = sheet.iter_rows()
        headers = [" ".join(cell.value.casefold().split()) if isinstance(cell.value, str) else ""
                   for cell in next(records, ())]
        required = ("admission number", "carry forward amount")
        if any(headers.count(header) != 1 for header in required):
            raise HTTPException(422, "Excel requires one Admission Number and one Carry Forward Amount column")
        admission_column, amount_column = (headers.index(header) for header in required)

        for row_number, cells in enumerate(records, start=2):
            if row_number > MAX_EXCEL_ROWS + 1:
                raise HTTPException(413, "Excel exceeds 1000 data rows")
            if amount_column >= len(cells):
                continue
            amount = cells[amount_column].value
            if amount is None or (isinstance(amount, str) and not amount.strip()):
                continue
            admission = cells[admission_column] if admission_column < len(cells) else None
            if cells[amount_column].data_type in ("f", "e") or (
                admission is not None and admission.data_type in ("f", "e")
            ):
                raise HTTPException(422, {"row": row_number, "message": "Formula and error cells are not supported"})
            admission_number = admission.value if admission is not None else None
            if type(admission_number) is int:
                admission_number = str(admission_number)
            elif isinstance(admission_number, float) and admission_number.is_integer():
                admission_number = str(int(admission_number))
            try:
                rows.append(CarryForwardUploadRow(admission_number=admission_number, amount=amount))
            except ValidationError as exc:
                raise HTTPException(422, {
                    "row": row_number,
                    "errors": exc.errors(include_context=False, include_input=False, include_url=False),
                }) from exc
    except (BadZipFile, InvalidFileException, IndexError, KeyError, OSError, ParseError, ValueError) as exc:
        raise HTTPException(422, "Could not read the Excel workbook") from exc
    finally:
        if workbook is not None:
            workbook.close()

    if not rows:
        raise HTTPException(422, "Enter a Carry Forward Amount for at least one row")
    try:
        return CarryForwardBulkWrite(rows=rows)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_context=False, include_input=False, include_url=False)) from exc


def save_carry_forwards(db, school_id, session_id, rows):
    scope = {"school_id": school_id, "session_id": session_id}
    admissions = [row.admission_number for row in rows]
    try:
        session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).first()
        if session is None:
            raise HTTPException(404, "School session not found")
        month = session.start_date.strftime("%Y-%m")
        students = dict(
            db.query(Student.admission_number, Student.id)
            .filter_by(**scope)
            .filter(Student.admission_number.in_(admissions))
            .order_by(Student.id)
            .with_for_update()
            .all()
        )
        missing = [admission for admission in admissions if admission not in students]
        if missing:
            raise HTTPException(404, {
                "message": "Admission numbers not found in this school session",
                "admission_numbers": missing,
            })

        saved = {
            item.student_id: item
            for item in db.query(StudentCarryForward)
            .filter_by(**scope)
            .filter(StudentCarryForward.student_id.in_(students.values()))
            .order_by(StudentCarryForward.student_id)
            .with_for_update()
            .all()
        }
        below_paid = [
            row.admission_number for row in rows
            if students[row.admission_number] in saved
            and row.amount < saved[students[row.admission_number]].paid_amount
        ]
        if below_paid:
            raise HTTPException(409, {
                "message": "Carry-forward amount cannot be less than the amount already paid",
                "admission_numbers": below_paid,
            })

        items = []
        for row in rows:
            student_id = students[row.admission_number]
            item = saved.get(student_id)
            if item is None:
                item = StudentCarryForward(**scope, student_id=student_id)
                db.add(item)
            item.month = month
            item.amount = row.amount
            items.append(item)
        db.flush()
        data = [
            {
                "id": item.id,
                "student_id": item.student_id,
                "admission_number": row.admission_number,
                "amount": item.amount,
                "paid_amount": item.paid_amount,
                "unpaid_amount": item.amount - item.paid_amount,
            }
            for row, item in zip(rows, items)
        ]
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Carry-forward upload conflicts with existing records") from exc
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Carry-forward fees saved successfully",
        **scope,
        "month": month,
        "total": len(data),
        "data": data,
    }

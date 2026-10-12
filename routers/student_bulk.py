"""Super Admin Excel import; duplicate admissions are skipped."""
import io
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School
from models.student import Student
from routers.students import validate_student_references, update_student_details
from schemas.student_bulk import StudentBulkRow, bulk_payload
from utils.dates import today

router = APIRouter()
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1000


@router.post('/students/bulk-upload', status_code=201)
def bulk_upload_students(
    school_id: int = Query(..., gt=0),
    class_id: int = Query(..., gt=0),
    section_id: int = Query(..., gt=0),
    session_id: int = Query(..., gt=0),
    caste_category_id: int | None = Query(None, gt=0),
    fee_category_id: int | None = Query(None, gt=0),
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
    user=Depends(get_current_user),
):
    from routers.super_admin import _require_super_admin
    _require_super_admin(user)
    if db.query(School.id).filter_by(id=school_id).first() is None:
        raise HTTPException(404, 'School not found')
    if not (file.filename or '').lower().endswith('.xlsx'):
        raise HTTPException(422, 'Upload an Excel .xlsx file')
    raw = file.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, 'Excel file exceeds 5 MB')
    failed_rows = []
    total = 0
    existing_admissions = {value for (value,) in db.query(Student.admission_number).filter_by(school_id=school_id).all()}
    workbook = None
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 50 * 1024 * 1024:
                raise HTTPException(413, 'Expanded Excel file exceeds 50 MB')
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
        sheet = workbook.worksheets[0]
        # Ignore misleading worksheet dimensions written by third-party exporters.
        sheet.reset_dimensions()
        records = sheet.iter_rows()
        header_cells = next(records, ())
        headers = [str(cell.value).strip() if cell.value is not None else '' for cell in header_cells]
        while headers and not headers[-1]:
            headers.pop()
        required = {'admission_number', 'father_name', 'mobile_number'}
        if not required.issubset(headers) or not {'name', 'first_name'}.intersection(headers):
            raise HTTPException(422, 'Excel requires admission_number, name (or first_name), father_name, mobile_number')
        if not all(headers) or len(headers) != len(set(headers)) or {'name', 'first_name'}.issubset(headers):
            raise HTTPException(422, 'Excel contains blank, duplicate or ambiguous headers')
        rows = []
        for number, cells in enumerate(records, start=2):
            if all(cell.value is None for cell in cells):
                continue
            total += 1
            if total > MAX_ROWS or number > MAX_ROWS + 1:
                raise HTTPException(413, 'Excel exceeds 1000 student rows')
            if any(cell.data_type in ('f', 'e') for cell in cells):
                raise HTTPException(422, {'row': number, 'message': 'Formula and error cells are not supported'})
            if any(cell.value is not None for cell in cells[len(headers):]):
                raise HTTPException(422, {'row': number, 'message': 'Data found without a column header'})
            row = {}
            for index, header in enumerate(headers):
                value = cells[index].value if index < len(cells) else None
                if isinstance(value, int) and not isinstance(value, bool):
                    value = str(value)
                elif isinstance(value, float) and value.is_integer():
                    value = str(int(value))
                row[header] = value
            admission = row.get('admission_number')
            admission = admission.strip() if isinstance(admission, str) else admission
            if admission and admission in existing_admissions:
                failed_rows.append({'row': number, 'admission_number': admission,
                                    'reason': 'Admission number already exists in this school'})
                continue
            try:
                rows.append((number, StudentBulkRow.model_validate(row)))
            except ValidationError as exc:
                raise HTTPException(422, {'row': number, 'errors': exc.errors(include_input=False, include_context=False)}) from exc
    except HTTPException:
        raise
    except (ParseError, BadZipFile, InvalidFileException, KeyError, ValueError, IndexError, OSError) as exc:
        raise HTTPException(422, 'Upload a valid Excel .xlsx workbook') from exc
    finally:
        if workbook is not None:
            workbook.close()
    if not total:
        raise HTTPException(422, 'Excel must contain at least one student')

    prepared = []
    seen = set()
    for number, row in rows:
        if row.admission_number in seen:
            failed_rows.append({'row': number, 'admission_number': row.admission_number,
                                'reason': 'Duplicate admission number in Excel'})
            continue
        seen.add(row.admission_number)
        try:
            payload = bulk_payload(row, session_id, class_id, section_id, caste_category_id, fee_category_id)
        except ValidationError as exc:
            raise HTTPException(422, {'row': number, 'errors': exc.errors(include_input=False, include_context=False)}) from exc
        except ValueError as exc:
            raise HTTPException(422, {'row': number, 'message': str(exc)}) from exc
        validate_student_references(db, school_id, payload)
        prepared.append((number, row, payload))

    try:
        db.query(School).filter_by(id=school_id).with_for_update().one()
        created = []
        for number, row, payload in prepared:
            if db.query(Student.id).filter_by(school_id=school_id, admission_number=row.admission_number).first():
                failed_rows.append({'row': number, 'admission_number': row.admission_number,
                                    'reason': 'Admission number already exists in this school'})
                continue
            values = payload.student_values()
            values['admission_date'] = values.get('admission_date') or today()
            item = Student(school_id=school_id, admission_number=row.admission_number, **values)
            # Follow the Schools create API for login, addresses and previous school.
            # The supplied admission number skips automatic admission allocation.
            update_student_details(db, school_id, item, payload)
            db.add(item)
            # Persist shared login IDs before processing the next sibling.
            db.flush()
            created.append(item)
        db.flush()
        result = [{'id': item.id, 'admission_number': item.admission_number,
                   'name': item.first_name, 'mobile_number': item.mobile_number} for item in created]
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Bulk upload conflicts with existing student records') from exc
    except Exception:
        db.rollback()
        raise
    return {'status': 'success', 'message': 'Students uploaded successfully',
            'school_id': school_id, 'session_id': session_id, 'class_id': class_id,
            'section_id': section_id, 'count': len(result), 'total': total,
            'inserted': len(result), 'failed': len(failed_rows),
            'failed_rows': sorted(failed_rows, key=lambda row: row['row']), 'data': result}

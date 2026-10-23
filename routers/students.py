from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from utils.passwords import hash_password
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School
from models.student import Student, StudentLogin, StudentAddressRecord, PreviousSchool
from models.user import User, UserRole
from schemas.student import StudentWrite, StudentResult, StudentListResult, StudentAdmissionUpdate, StudentDropdownResult, StudentStatusUpdate

router = APIRouter()


def student_school(school_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    # if current_user.role != UserRole.SUB_ADMIN:
    #     raise HTTPException(status_code=403, detail="Only Super Admin can access schools")
    if query.first() is None:
        raise HTTPException(404, "School not found")
    return db


def find_student(db, school_id, student_id):
    item = db.query(Student).filter_by(school_id=school_id, id=student_id).first()
    if item is None:
        raise HTTPException(404, "Student not found")
    return item


def save_student(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Student references conflict with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


@router.post("/{school_id}/students", response_model=StudentResult, status_code=201, include_in_schema=False)
@router.post("/students", response_model=StudentResult, status_code=201)
def create_student(school_id: int, payload: StudentWrite, db: Session = Depends(student_school)):
    validate_student_references(db, school_id, payload)
    values = payload.student_values()
    values["admission_date"] = values["admission_date"] or date.today()
    item = Student(school_id=school_id, **values)
    try:
        update_student_details(db, school_id, item, payload)
        return save_student(db, item, "Student created successfully")
    except Exception:
        db.rollback()
        raise


@router.get("/{school_id}/students", response_model=StudentListResult, include_in_schema=False)
@router.get("/students", response_model=StudentListResult)
def list_students(school_id: int, session_id: int | None = Query(None, gt=0),
                  status: bool = True, offset: int = Query(0, ge=0),
                  limit: int = Query(50, ge=1, le=200),
                  name: str | None = Query(None, min_length=1, max_length=767),
                  mobile: str | None = Query(None, min_length=1, max_length=20),
                  class_id: str = Query("all", max_length=100),
                  section_id: str = Query("all", max_length=100),
                  house_name: str = Query("all", max_length=100),
                  student_type: str = Query("all", max_length=100),
                  gender: str = Query("all", max_length=50),
                  religion: str = Query("all", max_length=100),
                  caste_category_name: str = Query("all", max_length=100),
                  fee_category_name: str = Query("all", max_length=100),
                  search: str | None = Query(None, min_length=1, max_length=767),
                  db: Session = Depends(student_school)):
    from dependencies.school_session import require_school_session
    query = db.query(Student).filter_by(school_id=school_id)
    if session_id is not None:
        require_school_session(db, school_id, session_id)
        query = query.filter(Student.session_id == session_id)
    query = query.filter(Student.status == ("active" if status else "inactive"))
    if name is not None:
        if not name.strip():
            raise HTTPException(422, "name must not be blank")
        # Match every name part, including records without a middle/last name.
        for part in name.split():
            query = query.filter(or_(
                Student.first_name.icontains(part, autoescape=True),
                Student.middle_name.icontains(part, autoescape=True),
                Student.last_name.icontains(part, autoescape=True),
            ))
    if mobile is not None:
        if not mobile.strip():
            raise HTTPException(422, "mobile must not be blank")
        query = query.filter(Student.mobile_number.contains(mobile.strip(), autoescape=True))
    # Dropdowns send their selected value; the default sentinel means "all".
    for field, value in (("class_id", class_id), ("section_id", section_id)):
        if value.strip().lower() != "all":
            try:
                identifier = int(value)
            except ValueError as exc:
                raise HTTPException(422, f"{field} must be a positive ID or 'all'") from exc
            if identifier <= 0:
                raise HTTPException(422, f"{field} must be a positive ID or 'all'")
            query = query.filter(getattr(Student, field) == identifier)
    for field, value in (("house_name", house_name), ("student_type", student_type),
                         ("gender", gender), ("religion", religion),
                         ("caste_category_name", caste_category_name),
                         ("fee_category_name", fee_category_name)):
        if value.strip().lower() != "all":
            query = query.filter(func.lower(func.trim(getattr(Student, field))) == value.strip().lower())
    if search is not None:
        from models.class_section import SchoolClass
        term = search.strip()
        if not term:
            raise HTTPException(422, "search must not be blank")
        name_match = and_(*(or_(
            Student.first_name.icontains(part, autoescape=True),
            Student.middle_name.icontains(part, autoescape=True),
            Student.last_name.icontains(part, autoescape=True),
        ) for part in term.split()))
        class_match = db.query(SchoolClass.id).filter(
            SchoolClass.id == Student.class_id,
            SchoolClass.school_id == Student.school_id,
            SchoolClass.session_id == Student.session_id,
            SchoolClass.name.icontains(term, autoescape=True),
        ).correlate(Student).exists()
        query = query.filter(or_(name_match,
                                 Student.mobile_number.contains(term, autoescape=True),
                                 class_match))
    # Report the count for the selected filters, before applying pagination.
    total_records = query.count()
    from models.class_section import SchoolClass, Section
    query = query.outerjoin(SchoolClass, Student.class_id == SchoolClass.id).outerjoin(
        Section, Student.section_id == Section.id,
    )
    ordering = (
        SchoolClass.class_order.asc().nulls_last(),
        func.lower(SchoolClass.name), SchoolClass.id,
        func.lower(Section.name).asc().nulls_last(), Section.id,
        func.lower(Student.first_name),
        func.lower(func.coalesce(Student.middle_name, "")),
        func.lower(func.coalesce(Student.last_name, "")), Student.id,
    )
    return {"message": "Students fetched successfully",
            "total_records": total_records,
            "data": query.order_by(*ordering).offset(offset).limit(limit).all()}


@router.get("/{school_id}/students/search", response_model=StudentListResult, include_in_schema=False)
@router.get("/students/search", response_model=StudentListResult)
def search_students(school_id: int, search: str = Query(..., min_length=1, max_length=767),
                    session_id: int | None = Query(None, gt=0), status: bool = True,
                    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                    db: Session = Depends(student_school)):
    return list_students(school_id=school_id, session_id=session_id, status=status,
                         offset=offset, limit=limit, name=None, mobile=None,
                         class_id="all", search=search, db=db)


@router.get("/{school_id}/students/dropdowns", response_model=StudentDropdownResult)
def student_dropdowns(school_id: int, session_id: int | None = Query(None, gt=0),
                      db: Session = Depends(student_school)):
    from dependencies.school_session import require_school_session
    from models.caste_category import CasteCategory
    from models.fee_category import FeeCategory
    from models.house import House
    from models.class_section import SchoolClass, Section

    if session_id is not None:
        require_school_session(db, school_id, session_id)
    data = {"school_id": school_id, "session_id": session_id}
    for key, model in (("caste_categories", CasteCategory), ("fee_categories", FeeCategory),
                       ("classes", SchoolClass), ("sections", Section), ("houses", House)):
        query = db.query(model).filter(model.school_id == school_id)
        if session_id is not None:
            query = query.filter(model.session_id == session_id)
        ordering = (model.class_order, model.name, model.id) if model is SchoolClass else (model.name, model.id)
        data[key] = query.order_by(*ordering).all()
    return {"message": "Student dropdowns fetched successfully", "data": data}


@router.get("/{school_id}/students/{student_id}", response_model=StudentResult)
def get_student(school_id: int, student_id: int, db: Session = Depends(student_school)):
    return {"message": "Student fetched successfully", "data": find_student(db, school_id, student_id)}


@router.put("/{school_id}/students/{student_id}", response_model=StudentResult, include_in_schema=False)
@router.put("/students", response_model=StudentResult)
def update_student(school_id: int, student_id: int, payload: StudentWrite, db: Session = Depends(student_school)):
    item = find_student(db, school_id, student_id)
    validate_student_references(db, school_id, payload)
    try:
        update_student_details(db, school_id, item, payload)
        for key, value in payload.student_values().items():
            if key in {"caste_category_name", "fee_category_name", "house_name"}:
                continue
            if key == "admission_date" and value is None:
                continue
            setattr(item, key, value)
        return save_student(db, item, "Student updated successfully")
    except Exception:
        db.rollback()
        raise




@router.patch("/{school_id}/students/{student_id}/admission-number", response_model=StudentResult, include_in_schema=False)
@router.patch("/students/admission-number", response_model=StudentResult)
def update_admission_number(school_id: int, student_id: int, payload: StudentAdmissionUpdate,
                            db: Session = Depends(student_school),
                            current_user: User = Depends(get_current_user)):
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(403, "Only Super Admin can edit admission numbers")
    # Use the same school lock as automatic allocation.
    db.query(School).filter_by(id=school_id).with_for_update().one()
    item = find_student(db, school_id, student_id)
    if item.login is None:
        raise HTTPException(409, "Update the student's details to create a login before editing the admission number")
    duplicate = db.query(Student.id).filter(
        Student.school_id == school_id,
        Student.admission_number == payload.admission_number,
        Student.session_id == item.session_id,
        Student.id != student_id,
    ).first()
    if duplicate:
        raise HTTPException(409, "Admission number already exists in this school session")
    item.admission_number = payload.admission_number
    return save_student(db, item, "Admission number updated successfully")


@router.patch("/students/status", response_model=StudentResult)
def update_student_status(school_id: int, student_id: int, payload: StudentStatusUpdate,
                          session_id: int = Query(..., gt=0),
                          db: Session = Depends(student_school)):
    item = find_student(db, school_id, student_id)
    from dependencies.school_session import require_school_session
    require_school_session(db, school_id, session_id)
    if item.session_id != session_id:
        raise HTTPException(404, "Student not found in this session")
    item.status = "active" if payload.status else "inactive"
    action = "activated" if payload.status else "deactivated"
    return save_student(db, item, f"Student {action} successfully")


@router.delete("/students", status_code=204)
def delete_student(school_id: int, student_id: int, db: Session = Depends(student_school)):
    item = find_student(db, school_id, student_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Student is referenced by other records") from exc
    except Exception:
        db.rollback()
        raise


def validate_student_references(db, school_id, payload):
    from models.session import Session as SchoolSession
    from models.class_section import SchoolClass, Section
    for field, model in (("session_id", SchoolSession), ("class_id", SchoolClass), ("section_id", Section)):
        query = db.query(model.id).filter(model.id == getattr(payload.student_info, field), model.school_id == school_id)
        if model is not SchoolSession:
            query = query.filter(model.session_id == payload.student_info.session_id)
        if query.first() is None:
            raise HTTPException(404, f"{field} does not exist in this school session")
    resolve_student_dropdowns(db, school_id, payload)


def resolve_student_dropdowns(db, school_id, payload):
    from models.caste_category import CasteCategory
    from models.fee_category import FeeCategory
    from models.house import House

    references = {}
    for field, model in (("caste_category", CasteCategory), ("fee_category", FeeCategory), ("house", House)):
        name = getattr(payload.student_info, f"{field}_name", None)
        if name is None:
            references[field] = None
            continue
        query = db.query(model).filter_by(school_id=school_id, session_id=payload.student_info.session_id)
        if name is not None:
            query = query.filter(model.name == name)
        reference = query.first()
        if reference is None:
            key = f"{field}_name"
            raise HTTPException(404, f"{key} does not exist in this school session")
        references[field] = reference
    return references


def update_student_details(db, school_id, item, payload):
    for field, reference in resolve_student_dropdowns(db, school_id, payload).items():
        setattr(item, f"{field}_name", reference.name if reference is not None else None)

    if item.admission_number is None:
        # Serialize admission allocation per school until the transaction commits.
        school = db.query(School).filter_by(id=school_id).populate_existing().with_for_update().one()
        last = db.query(func.max(Student.admission_sequence)).filter_by(school_id=school_id).scalar()
        sequence = max(school.start_admission_no, last + 1 if last is not None else school.start_admission_no)
        # Manual edits can reserve a number that automatic allocation would reach.
        while db.query(Student.id).filter_by(
            school_id=school_id,
            admission_number=f"{school.admission_prefix or ''}{sequence}{school.admission_suffix or ''}",
        ).first() is not None:
            sequence += 1
        item.admission_sequence = sequence
        item.admission_number = f"{school.admission_prefix or ''}{sequence}{school.admission_suffix or ''}"
    # Serialize lookup/create for siblings and mobile changes within this school.
    with db.no_autoflush:
        db.query(School).filter_by(id=school_id).with_for_update().one()
        account = db.query(StudentLogin).filter_by(
            school_id=school_id, mobile=payload.student_info.mobile_number,
        ).first()
    if account is None:
        account = StudentLogin(
            school_id=school_id, mobile=payload.student_info.mobile_number,
            password=hash_password(payload.student_info.mobile_number),
        )
    item.login = account
    existing = {address.address_type: address for address in item.addresses}
    for kind, address in (("present", payload.present_address),
                          ("permanent", payload.permanent_address)):
        record = existing.get(kind)
        if record is None:
            record = StudentAddressRecord(address_type=kind)
            item.addresses.append(record)
        for key, value in address.model_dump().items():
            setattr(record, key, value)

    if payload.previous_school is None or not any(payload.previous_school.model_dump().values()):
        item.previous_school = None
    else:
        if item.previous_school is None:
            item.previous_school = PreviousSchool(school_id=school_id)
        for key, value in payload.previous_school.model_dump().items():
            setattr(item.previous_school, key, value)

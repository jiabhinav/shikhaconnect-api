from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolUserAssignment
from models.student import Student
from models.user import User, UserRole
from schemas.student import StudentWrite, StudentResult, StudentListResult

router = APIRouter()


def student_school(school_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage students")
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
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


@router.post("/school/{school_id}/students", response_model=StudentResult, status_code=201)
def create_student(school_id: int, payload: StudentWrite, db: Session = Depends(student_school)):
    validate_student_references(db, school_id, payload)
    return save_student(db, Student(school_id=school_id, **payload.student_values()), "Student created successfully")


@router.get("/school/{school_id}/students", response_model=StudentListResult)
def list_students(school_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), db: Session = Depends(student_school)):
    return {"message": "Students fetched successfully", "data": db.query(Student).filter_by(
        school_id=school_id).order_by(Student.id).offset(offset).limit(limit).all()}


@router.get("/school/{school_id}/students/{student_id}", response_model=StudentResult)
def get_student(school_id: int, student_id: int, db: Session = Depends(student_school)):
    return {"message": "Student fetched successfully", "data": find_student(db, school_id, student_id)}


@router.put("/school/{school_id}/students/{student_id}", response_model=StudentResult)
def update_student(school_id: int, student_id: int, payload: StudentWrite, db: Session = Depends(student_school)):
    item = find_student(db, school_id, student_id)
    validate_student_references(db, school_id, payload)
    for key, value in payload.student_values().items():
        setattr(item, key, value)
    return save_student(db, item, "Student updated successfully")




def validate_student_references(db, school_id, payload):
    from models.session import Session as SchoolSession
    from models.class_section import SchoolClass
    from models.fee_category import FeeCategory
    from models.caste_category import CasteCategory

    for field, model in (("session_id", SchoolSession), ("class_id", SchoolClass),
                         ("fee_category_id", FeeCategory), ("caste_category_id", CasteCategory)):
        if db.query(model.id).filter(model.id == getattr(payload.student_info, field), model.school_id == school_id).first() is None:
            raise HTTPException(404, f"{field} does not exist in this school")

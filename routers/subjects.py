import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database.subject_table import ensure_subject_table
from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolUserAssignment
from models.subject import Subject
from models.user import User, UserRole
from schemas.subject import SubjectWrite, SubjectStatusUpdate, SubjectStatus, SubjectResult, SubjectListResult

router = APIRouter()


def subject_storage(school_id: int, db: Session = Depends(get_db_session),
                    current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage subjects")
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School not found")
    try:
        ensure_subject_table(db.connection())
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logging.getLogger(__name__).exception("Subject table initialization failed")
        raise HTTPException(503, "Subject storage is unavailable") from exc
    return db


def find_subject(db, school_id, subject_id):
    item = db.query(Subject).filter_by(school_id=school_id, id=subject_id).first()
    if item is None:
        raise HTTPException(404, "Subject not found")
    return item


def save_subject(db, item):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Subject name or code already exists for this school, or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": "Subject saved successfully", "data": item}


@router.post("/school/{school_id}/subjects", response_model=SubjectResult, status_code=201)
def create_subject(school_id: int, payload: SubjectWrite, db: Session = Depends(subject_storage)):
    return save_subject(db, Subject(school_id=school_id, **payload.model_dump()))


@router.get("/school/{school_id}/subjects", response_model=SubjectListResult)
def list_subjects(school_id: int, status: SubjectStatus | None = None, db: Session = Depends(subject_storage)):
    query = db.query(Subject).filter_by(school_id=school_id)
    if status is not None:
        query = query.filter(Subject.status == status)
    return {"message": "Subjects fetched successfully", "data": query.order_by(Subject.id).all()}


@router.put("/school/{school_id}/subjects/{subject_id}", response_model=SubjectResult)
def update_subject(school_id: int, subject_id: int, payload: SubjectWrite, db: Session = Depends(subject_storage)):
    item = find_subject(db, school_id, subject_id)
    item.name = payload.name
    item.code = payload.code
    # A name/code edit should not reactivate an inactive subject implicitly.
    if "status" in payload.model_fields_set:
        item.status = payload.status
    return save_subject(db, item)


@router.patch("/school/{school_id}/subjects/{subject_id}/status", response_model=SubjectResult)
def update_subject_status(school_id: int, subject_id: int, payload: SubjectStatusUpdate, db: Session = Depends(subject_storage)):
    item = find_subject(db, school_id, subject_id)
    item.status = payload.status
    return save_subject(db, item)


@router.delete("/school/{school_id}/subjects/{subject_id}")
def delete_subject(school_id: int, subject_id: int, db: Session = Depends(subject_storage)):
    item = find_subject(db, school_id, subject_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Subject is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Subject deleted successfully", "data": {"id": subject_id}}

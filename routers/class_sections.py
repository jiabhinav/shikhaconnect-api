import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database.class_section_tables import ensure_class_section_tables
from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.class_section import SchoolClass, Section
from models.school import School, SchoolUserAssignment
from models.user import User, UserRole
from schemas.class_section import (
    ClassWrite, SectionWrite, ClassResult, ClassListResult, SectionResult, SectionListResult,
)

router = APIRouter()


def school_storage(school_id: int, db: Session = Depends(get_db_session),
                   current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School not found")
    try:
        ensure_class_section_tables(db.connection())
        # Persist creation even when the first request only lists records.
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logging.getLogger(__name__).exception("Class/section table initialization failed")
        raise HTTPException(503, "Class and section storage is unavailable") from exc
    return db


def record(db, model, school_id, record_id):
    item = db.query(model).filter_by(school_id=school_id, id=record_id).first()
    if item is None:
        raise HTTPException(404, "Class or section not found")
    return item


def save(db, item):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Name already exists for this school or the record conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": "Saved successfully", "data": item}


def remove(db, item):
    item_id = item.id
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "This record is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Deleted successfully", "data": {"id": item_id}}


@router.post("/school/{school_id}/classes", response_model=ClassResult, status_code=201)
def create_class(school_id: int, payload: ClassWrite, db: Session = Depends(school_storage)):
    # Serialize automatic order assignment for concurrent creates in one school.
    db.query(School).filter_by(id=school_id).with_for_update().first()
    order = payload.class_order
    if order is None:
        order = (db.query(func.max(SchoolClass.class_order)).filter_by(school_id=school_id).scalar() or 0) + 1
    return save(db, SchoolClass(school_id=school_id, name=payload.name, class_order=order))


@router.get("/school/{school_id}/classes", response_model=ClassListResult)
def list_classes(school_id: int, db: Session = Depends(school_storage)):
    return {"message": "Classes fetched successfully", "data": db.query(SchoolClass).filter_by(
        school_id=school_id).order_by(SchoolClass.class_order, SchoolClass.id).all()}


@router.put("/school/{school_id}/classes/{class_id}", response_model=ClassResult)
def update_class(school_id: int, class_id: int, payload: ClassWrite, db: Session = Depends(school_storage)):
    item = record(db, SchoolClass, school_id, class_id)
    item.name = payload.name
    if payload.class_order is not None:
        item.class_order = payload.class_order
    return save(db, item)


@router.delete("/school/{school_id}/classes/{class_id}")
def delete_class(school_id: int, class_id: int, db: Session = Depends(school_storage)):
    return remove(db, record(db, SchoolClass, school_id, class_id))


@router.post("/school/{school_id}/sections", response_model=SectionResult, status_code=201)
def create_section(school_id: int, payload: SectionWrite, db: Session = Depends(school_storage)):
    return save(db, Section(school_id=school_id, name=payload.name))


@router.get("/school/{school_id}/sections", response_model=SectionListResult)
def list_sections(school_id: int, db: Session = Depends(school_storage)):
    return {"message": "Sections fetched successfully", "data": db.query(Section).filter_by(
        school_id=school_id).order_by(Section.id).all()}


@router.put("/school/{school_id}/sections/{section_id}", response_model=SectionResult)
def update_section(school_id: int, section_id: int, payload: SectionWrite, db: Session = Depends(school_storage)):
    item = record(db, Section, school_id, section_id)
    item.name = payload.name
    return save(db, item)


@router.delete("/school/{school_id}/sections/{section_id}")
def delete_section(school_id: int, section_id: int, db: Session = Depends(school_storage)):
    return remove(db, record(db, Section, school_id, section_id))

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolUserAssignment
from models.caste_category import CasteCategory
from models.user import User, UserRole
from schemas.caste_category import CasteCategoryWrite, CasteCategoryResult, CasteCategoryListResult

router = APIRouter()


def caste_category_school(school_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage caste categories")
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School not found")
    return db


def find_caste_category(db, school_id, caste_category_id):
    item = db.query(CasteCategory).filter_by(school_id=school_id, id=caste_category_id).first()
    if item is None:
        raise HTTPException(404, "Caste category not found")
    return item


def save_caste_category(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Caste category name already exists for this school or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


@router.post("/school/{school_id}/caste_categories", response_model=CasteCategoryResult, status_code=201)
def create_caste_category(school_id: int, payload: CasteCategoryWrite, db: Session = Depends(caste_category_school)):
    return save_caste_category(db, CasteCategory(school_id=school_id, name=payload.name), "Caste category created successfully")


@router.get("/school/{school_id}/caste_categories", response_model=CasteCategoryListResult)
def list_caste_categories(school_id: int, db: Session = Depends(caste_category_school)):
    return {"message": "Caste categories fetched successfully", "data": db.query(CasteCategory).filter_by(
        school_id=school_id).order_by(CasteCategory.id).all()}


@router.get("/school/{school_id}/caste_categories/{caste_category_id}", response_model=CasteCategoryResult)
def get_caste_category(school_id: int, caste_category_id: int, db: Session = Depends(caste_category_school)):
    return {"message": "Caste category fetched successfully", "data": find_caste_category(db, school_id, caste_category_id)}


@router.put("/school/{school_id}/caste_categories/{caste_category_id}", response_model=CasteCategoryResult)
def update_caste_category(school_id: int, caste_category_id: int, payload: CasteCategoryWrite, db: Session = Depends(caste_category_school)):
    item = find_caste_category(db, school_id, caste_category_id)
    item.name = payload.name
    return save_caste_category(db, item, "Caste category updated successfully")


@router.delete("/school/{school_id}/caste_categories/{caste_category_id}")
def delete_caste_category(school_id: int, caste_category_id: int, db: Session = Depends(caste_category_school)):
    item = find_caste_category(db, school_id, caste_category_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Caste category is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Caste category deleted successfully", "data": {"id": caste_category_id}}

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolUserAssignment
from models.fee_category import FeeCategory
from models.user import User, UserRole
from schemas.fee_category import FeeCategoryWrite, FeeCategoryResult, FeeCategoryListResult

router = APIRouter()


def fee_category_school(school_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage fee categories")
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School not found")
    return db


def find_fee_category(db, school_id, fee_category_id):
    item = db.query(FeeCategory).filter_by(school_id=school_id, id=fee_category_id).first()
    if item is None:
        raise HTTPException(404, "Fee category not found")
    return item


def save_fee_category(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee category name already exists for this school or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


@router.post("/school/{school_id}/fee_categories", response_model=FeeCategoryResult, status_code=201)
def create_fee_category(school_id: int, payload: FeeCategoryWrite, db: Session = Depends(fee_category_school)):
    return save_fee_category(db, FeeCategory(school_id=school_id, name=payload.name), "Fee category created successfully")


@router.get("/school/{school_id}/fee_categories", response_model=FeeCategoryListResult)
def list_fee_categories(school_id: int, db: Session = Depends(fee_category_school)):
    return {"message": "Fee categories fetched successfully", "data": db.query(FeeCategory).filter_by(
        school_id=school_id).order_by(FeeCategory.id).all()}


@router.get("/school/{school_id}/fee_categories/{fee_category_id}", response_model=FeeCategoryResult)
def get_fee_category(school_id: int, fee_category_id: int, db: Session = Depends(fee_category_school)):
    return {"message": "Fee category fetched successfully", "data": find_fee_category(db, school_id, fee_category_id)}


@router.put("/school/{school_id}/fee_categories/{fee_category_id}", response_model=FeeCategoryResult)
def update_fee_category(school_id: int, fee_category_id: int, payload: FeeCategoryWrite, db: Session = Depends(fee_category_school)):
    item = find_fee_category(db, school_id, fee_category_id)
    item.name = payload.name
    return save_fee_category(db, item, "Fee category updated successfully")


@router.delete("/school/{school_id}/fee_categories/{fee_category_id}")
def delete_fee_category(school_id: int, fee_category_id: int, db: Session = Depends(fee_category_school)):
    item = find_fee_category(db, school_id, fee_category_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee category is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Fee category deleted successfully", "data": {"id": fee_category_id}}

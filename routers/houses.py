from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolUserAssignment
from models.house import House
from models.user import User, UserRole
from schemas.house import HouseWrite, HouseResult, HouseListResult

router = APIRouter()


def house_school(school_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage houses")
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School not found")
    return db


def find_house(db, school_id, house_id):
    item = db.query(House).filter_by(school_id=school_id, id=house_id).first()
    if item is None:
        raise HTTPException(404, "House not found")
    return item


def save_house(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "House name already exists for this school or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


@router.post("/school/{school_id}/houses", response_model=HouseResult, status_code=201)
def create_house(school_id: int, payload: HouseWrite, db: Session = Depends(house_school)):
    return save_house(db, House(school_id=school_id, name=payload.name), "House created successfully")


@router.get("/school/{school_id}/houses", response_model=HouseListResult)
def list_houses(school_id: int, db: Session = Depends(house_school)):
    return {"message": "Houses fetched successfully", "data": db.query(House).filter_by(
        school_id=school_id).order_by(House.id).all()}


@router.get("/school/{school_id}/houses/{house_id}", response_model=HouseResult)
def get_house(school_id: int, house_id: int, db: Session = Depends(house_school)):
    return {"message": "House fetched successfully", "data": find_house(db, school_id, house_id)}


@router.put("/school/{school_id}/houses/{house_id}", response_model=HouseResult)
def update_house(school_id: int, house_id: int, payload: HouseWrite, db: Session = Depends(house_school)):
    item = find_house(db, school_id, house_id)
    item.name = payload.name
    return save_house(db, item, "House updated successfully")


@router.delete("/school/{school_id}/houses/{house_id}")
def delete_house(school_id: int, house_id: int, db: Session = Depends(house_school)):
    item = find_house(db, school_id, house_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "House is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "House deleted successfully", "data": {"id": house_id}}

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.user import LoginUser, User, UserRole
from schemas.school_mapping import (
    SchoolMappingWrite, SchoolMappingStatusUpdate, SchoolMappingResult, SchoolUserListResult,
)

router = APIRouter()


def mapping_db(db: Session = Depends(get_db_session), current_user: User = Depends(get_current_user)):
    role = getattr(current_user.role, "value", current_user.role)
    if role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(403, "Only Super Admin can manage school mappings")
    return db


def require_school(db, school_id):
    if db.get(School, school_id) is None:
        raise HTTPException(404, "School not found")


def validate_assignment(db, payload, mapping_id=None):
    require_school(db, payload.school_id)
    user = db.query(LoginUser).filter_by(id=payload.user_id).with_for_update(of=LoginUser).first()
    if user is None:
        raise HTTPException(404, "Login user not found")
    query = db.query(SchoolMapping).filter_by(school_id=payload.school_id, user_id=user.id)
    if mapping_id is not None:
        query = query.filter(SchoolMapping.id != mapping_id)
    if query.first() is not None:
        raise HTTPException(409, "User is already assigned to this school")
    if user.role == UserRole.SUB_ADMIN:
        assignments = db.query(SchoolMapping).filter_by(user_id=user.id)
        if mapping_id is not None:
            assignments = assignments.filter(SchoolMapping.id != mapping_id)
        if assignments.first() is not None:
            raise HTTPException(409, "Sub Admin can only be assigned to one school")

    return user


def find_mapping(db, mapping_id):
    item = db.get(SchoolMapping, mapping_id)
    if item is None:
        raise HTTPException(404, "School mapping not found")
    return item


def save_mapping(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "School mapping conflicts with an existing assignment or database constraint") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": {
        "id": item.id, "school_id": item.school_id,
        "user_id": item.user_id, "status": item.status,
    }}


@router.post("/school-mappings", response_model=SchoolMappingResult, status_code=201)
def create_mapping(payload: SchoolMappingWrite, db: Session = Depends(mapping_db)):
    user = validate_assignment(db, payload)
    values = payload.model_dump()
    values["user_id"] = user.id
    return save_mapping(db, SchoolMapping(**values), "User assigned to school successfully")


@router.put("/school-mappings/{mapping_id}", response_model=SchoolMappingResult)
def update_mapping(mapping_id: int, payload: SchoolMappingWrite, db: Session = Depends(mapping_db)):
    item = find_mapping(db, mapping_id)
    user = validate_assignment(db, payload, mapping_id)
    values = payload.model_dump()
    values["user_id"] = user.id
    for key, value in values.items():
        setattr(item, key, value)
    return save_mapping(db, item, "School mapping updated successfully")


@router.patch("/school-mappings/{mapping_id}/status", response_model=SchoolMappingResult)
def update_mapping_status(mapping_id: int, payload: SchoolMappingStatusUpdate, db: Session = Depends(mapping_db)):
    item = find_mapping(db, mapping_id)
    item.status = payload.status
    return save_mapping(db, item, "School mapping status updated successfully")


@router.delete("/school-mappings/{mapping_id}")
def delete_mapping(mapping_id: int, db: Session = Depends(mapping_db)):
    item = find_mapping(db, mapping_id)
    try:
        db.delete(item)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "School mapping deleted successfully", "data": {"id": mapping_id}}


@router.get("/school/users", response_model=SchoolUserListResult)
def list_school_users(school_id: int, status: SchoolMappingStatus | None = None,
                      db: Session = Depends(mapping_db)):
    require_school(db, school_id)
    query = db.query(SchoolMapping, LoginUser).join(LoginUser, LoginUser.id == SchoolMapping.user_id).filter(
        SchoolMapping.school_id == school_id)
    if status is not None:
        query = query.filter(SchoolMapping.status == status)
    data = [
        {"id": item.id, "school_id": item.school_id, "user_id": user.id, "status": item.status,
         "first_name": user.first_name, "middle_name": user.middle_name, "last_name": user.last_name,
         "email": user.email, "mobile": user.mobile, "role": user.role, "user_status": user.status}
        for item, user in query.order_by(SchoolMapping.id).all()
    ]
    return {"message": "School users fetched successfully", "data": data}

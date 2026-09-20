from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import MetaData, Table, select
from sqlalchemy.exc import IntegrityError, NoSuchTableError
from sqlalchemy.orm import Session, selectinload

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.caste_category import CasteCategory
from models.school import School
from models.staff import Staff, StaffAddress, StaffPermission
from models.user import User, UserRole
from schemas.staff import StaffCreate, StaffListResult, StaffResult
from database.module_names import get_module_names

router = APIRouter()


def staff_school(school_id: int, db: Session = Depends(get_db_session),
                 current_user: User = Depends(get_current_user)):
    # if current_user.role != UserRole.SUPER_ADMIN:
    #     raise HTTPException(403, "Only Super Admin can access schools")
    if db.get(School, school_id) is None:
        raise HTTPException(404, "School not found")
    return db


def validate_references(db, school_id, payload):
    category = db.query(CasteCategory.id).filter_by(
        id=payload.staff_info.caste_category_id, school_id=school_id
    ).first()
    if category is None:
        raise HTTPException(404, "caste_category_id does not exist in this school")
    staff_module_ids = {permission.staff_module_id for permission in payload.permissions}
    if not staff_module_ids:
        return
    try:
        modules = Table("staff_modules", MetaData(), autoload_with=db.connection())
    except NoSuchTableError as exc:
        raise HTTPException(503, "Staff modules table is unavailable") from exc
    if "id" not in modules.c:
        raise HTTPException(503, "Staff modules ID column is unavailable")
    if "status" in modules.c:
        active = modules.c.status.is_(True)
    elif "is_active" in modules.c:
        active = modules.c.is_active.is_(True)
    else:
        raise HTTPException(503, "Staff modules active status column is unavailable")
    available = set(db.execute(select(modules.c.id).where(
        modules.c.id.in_(staff_module_ids), active
    )).scalars())
    if staff_module_ids - available:
        raise HTTPException(422, "Permissions must reference existing active staff modules")


@router.post("/school/{school_id}/staff", response_model=StaffResult, status_code=201)
def create_staff(school_id: int, payload: StaffCreate, db: Session = Depends(staff_school)):
    validate_references(db, school_id, payload)
    item = Staff(school_id=school_id, **payload.staff_info.model_dump())
    item.address = StaffAddress(**payload.address.model_dump())
    item.permissions = [StaffPermission(**permission.model_dump()) for permission in payload.permissions]
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Staff references conflict with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    # Attach module names for permissions in response
    module_ids = [p.staff_module_id for p in item.permissions]
    names = get_module_names(db, module_ids)
    for p in item.permissions:
        setattr(p, "name", names.get(p.staff_module_id))
    return {"message": "Staff created successfully", "data": item}


@router.get("/school/{school_id}/staff", response_model=StaffListResult)
def list_staff(school_id: int, offset: int = Query(0, ge=0),
               limit: int = Query(50, ge=1, le=200), db: Session = Depends(staff_school)):
    items = db.query(Staff).options(selectinload(Staff.address), selectinload(Staff.permissions)).filter_by(
        school_id=school_id
    ).order_by(Staff.id).offset(offset).limit(limit).all()
    # Populate permission names
    all_module_ids = [p.staff_module_id for item in items for p in item.permissions]
    names = get_module_names(db, all_module_ids)
    for item in items:
        for p in item.permissions:
            setattr(p, "name", names.get(p.staff_module_id))
    return {"message": "Staff fetched successfully", "data": items}


@router.get("/school/{school_id}/staff/{staff_id}", response_model=StaffResult)
def get_staff(school_id: int, staff_id: int, db: Session = Depends(staff_school)):
    item = db.query(Staff).filter_by(id=staff_id, school_id=school_id).first()
    if item is None:
        raise HTTPException(404, "Staff not found")
    module_ids = [p.staff_module_id for p in item.permissions]
    names = get_module_names(db, module_ids)
    for p in item.permissions:
        setattr(p, "name", names.get(p.staff_module_id))
    return {"message": "Staff fetched successfully", "data": item}


@router.put("/school/{school_id}/staff/{staff_id}", response_model=StaffResult)
def update_staff(school_id: int, staff_id: int, payload: StaffCreate, db: Session = Depends(staff_school)):
    item = db.query(Staff).options(selectinload(Staff.address), selectinload(Staff.permissions)).filter_by(
        id=staff_id, school_id=school_id
    ).first()
    if item is None:
        raise HTTPException(404, "Staff not found")

    validate_references(db, school_id, payload)

    # Update staff fields
    for field, value in payload.staff_info.model_dump().items():
        setattr(item, field, value)

    # Update or create address
    address_values = payload.address.model_dump()
    if item.address is None:
        item.address = StaffAddress(**address_values)
    else:
        for k, v in address_values.items():
            setattr(item.address, k, v)

    # Replace permissions atomically
    item.permissions.clear()
    db.flush()
    item.permissions = [StaffPermission(**permission.model_dump()) for permission in payload.permissions]

    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Staff references conflict with database constraints") from exc
    except Exception:
        db.rollback()
        raise

    # attach module names
    module_ids = [p.staff_module_id for p in item.permissions]
    names = get_module_names(db, module_ids)
    for p in item.permissions:
        setattr(p, "name", names.get(p.staff_module_id))

    return {"status": "success", "message": "Staff updated successfully", "data": item}


@router.delete("/school/{school_id}/staff/{staff_id}")
def delete_staff(school_id: int, staff_id: int, db: Session = Depends(staff_school)):
    item = db.query(Staff).filter_by(id=staff_id, school_id=school_id).first()
    if item is None:
        raise HTTPException(404, "Staff not found")

    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Staff is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise

    return {"status": "success", "message": "Staff deleted successfully", "data": {"id": staff_id}}

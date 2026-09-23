from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import MetaData, Table, select
from sqlalchemy.exc import IntegrityError, NoSuchTableError
from sqlalchemy.orm import Session, contains_eager, selectinload

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.caste_category import CasteCategory
from models.school import School
from models.staff import Staff, StaffAddress, StaffPermission
from models.user import LoginUser, User, UserRole
from utils.passwords import hash_password
from schemas.staff import StaffCreate, StaffListResult, StaffResult
from database.module_names import get_staff_module_names

router = APIRouter()


def staff_constraint_error(exc):
    """Expose constraint identifiers, never SQL parameters or account data."""
    original = exc.orig
    code = getattr(original, "pgcode", None)
    diagnostic = getattr(original, "diag", None)
    table = getattr(diagnostic, "table_name", None)
    column = getattr(diagnostic, "column_name", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    messages = {
        "23505": "A record already exists for a unique field",
        "23503": "A referenced record does not exist or is still in use",
        "23502": "A required database column has no value; check the database migration",
        "23514": "A value violates a database check constraint",
    }
    if code not in messages:
        return HTTPException(409, "Staff references conflict with database constraints")
    detail = {"message": messages[code], "code": code}
    for key, value in (("table", table), ("column", column), ("constraint", constraint)):
        if value:
            detail[key] = value
    return HTTPException(409, detail)


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


def apply_staff_profile(db, payload, item=None):
    values = payload.staff_info.model_dump()
    password = values.pop("password", None)
    values["mobile"] = values.pop("mobile_number")
    account = item.login_user if item is not None else None
    for field in ("email", "mobile"):
        query = db.query(LoginUser).filter(getattr(LoginUser, field) == values[field])
        if account is not None:
            query = query.filter(LoginUser.id != account.id)
        if query.first():
            raise HTTPException(409, f"{field.capitalize()} already exists")
    account_values = {field: values.pop(field) for field in
                      ("first_name", "middle_name", "last_name", "email", "mobile", "role")}
    if item is None:
        account = LoginUser(**account_values, password=hash_password(password or account_values["mobile"]))
        item = Staff(login_user=account)
    else:
        for field, value in account_values.items():
            setattr(account, field, value)
        if password is not None:
            account.password = hash_password(password)
    for field, value in values.items():
        setattr(item, field, value)
    return item


@router.post("/school/{school_id}/staff", response_model=StaffResult, status_code=201)
def create_staff(school_id: int, payload: StaffCreate, db: Session = Depends(staff_school)):
    validate_references(db, school_id, payload)
    item = apply_staff_profile(db, payload)
    item.school_id = school_id
    # Ignore any incoming address `id`/`staff_id` and let the relationship assign login_user_id
    addr_vals = payload.address.model_dump()
    addr_vals.pop("id", None)
    addr_vals.pop("login_user_id", None)
    item.address = StaffAddress(**addr_vals)
    item.permissions = [StaffPermission(**permission.model_dump()) for permission in payload.permissions]
    try:
        db.add(item)
        db.flush()
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise staff_constraint_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    # Attach module names for permissions in response
    module_ids = [p.staff_module_id for p in item.permissions]
    names = get_staff_module_names(db, module_ids)
    for p in item.permissions:
        setattr(p, "name", names.get(p.staff_module_id))
    return {"message": "Staff created successfully", "data": item}


@router.get("/school/{school_id}/staff", response_model=StaffListResult)
def list_staff(school_id: int, offset: int = Query(0, ge=0),
               limit: int = Query(50, ge=1, le=200), db: Session = Depends(staff_school)):
    # Include legacy role spellings accepted by the account model as well.
    admin_roles = [
        "Super Admin", "Admin", "Sub Admin",
        "SUPER_ADMIN", "ADMIN", "SUB_ADMIN",
        "SuperAdmin", "SubAdmin",
    ]
    items = (
        db.query(Staff)
        .select_from(LoginUser)
        .join(Staff, Staff.login_user_id == LoginUser.id)
        .options(
            contains_eager(Staff.login_user).joinedload(LoginUser.address),
            contains_eager(Staff.login_user).selectinload(LoginUser.permissions),
        )
        .filter(Staff.school_id == school_id, LoginUser.role.notin_(admin_roles))
        .order_by(Staff.id).offset(offset).limit(limit).all()
    )
    # Populate permission names
    all_module_ids = [p.staff_module_id for item in items for p in item.permissions]
    names = get_staff_module_names(db, all_module_ids)
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
    names = get_staff_module_names(db, module_ids)
    for p in item.permissions:
        setattr(p, "name", names.get(p.staff_module_id))
    return {"message": "Staff fetched successfully", "data": item}


@router.put("/school/{school_id}/staff/{staff_id}", response_model=StaffResult)
def update_staff(school_id: int, staff_id: int, payload: StaffCreate, db: Session = Depends(staff_school)):
    item = db.query(Staff).options(selectinload(Staff.login_user).selectinload(LoginUser.address), selectinload(Staff.login_user).selectinload(LoginUser.permissions)).filter_by(
        id=staff_id, school_id=school_id
    ).first()
    if item is None:
        raise HTTPException(404, "Staff not found")

    validate_references(db, school_id, payload)

    apply_staff_profile(db, payload, item)

    # Update or create address. Ignore incoming `id`/`staff_id` to avoid PK inconsistencies.
    address_values = payload.address.model_dump()
    if item.address is None:
        av = address_values.copy()
        av.pop("id", None)
        av.pop("login_user_id", None)
        item.address = StaffAddress(**av)
        # ensure FK points to this staff
        item.address.login_user = item.login_user
    else:
        for k, v in address_values.items():
            if k in ("id", "login_user_id"):
                continue
            setattr(item.address, k, v)
        # ensure FK remains correct
        item.address.login_user = item.login_user

    # Replace permissions atomically
    try:
        item.permissions.clear()
        db.flush()
        item.permissions = [StaffPermission(**permission.model_dump()) for permission in payload.permissions]
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise staff_constraint_error(exc) from exc
    except Exception:
        db.rollback()
        raise

    # attach module names
    module_ids = [p.staff_module_id for p in item.permissions]
    names = get_staff_module_names(db, module_ids)
    for p in item.permissions:
        setattr(p, "name", names.get(p.staff_module_id))

    return {"status": "success", "message": "Staff updated successfully", "data": item}


@router.delete("/school/{school_id}/staff/{staff_id}")
def delete_staff(school_id: int, staff_id: int, db: Session = Depends(staff_school)):
    item = db.query(Staff).filter_by(id=staff_id, school_id=school_id).first()
    if item is None:
        raise HTTPException(404, "Staff not found")

    try:
        account = item.login_user
        db.delete(item)
        db.flush()
        # Old migrations may have left a user profile sharing this account.
        if not db.query(User.id).filter_by(login_user_id=account.id).first():
            db.delete(account)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Staff is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise

    return {"status": "success", "message": "Staff deleted successfully", "data": {"id": staff_id}}

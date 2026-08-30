from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolPermission, SchoolUserAssignment
from models.user import User, UserRole, UserStatus
from schemas.school import SchoolCreate

router = APIRouter(
    prefix="/schools",
    tags=["Schools"],
)


def _role_value(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else str(user.role)


def _require_super_admin(user: User) -> None:
    if _role_value(user) != UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Super Admin can create schools")


def _school_payload(school: School) -> dict:
    admins = []
    sub_admins = []
    for assignment in school.assignments:
        item = {
            "id": assignment.user.id,
            "name": " ".join(filter(None, [assignment.user.first_name, assignment.user.middle_name, assignment.user.last_name])),
            "email": assignment.user.email,
        }
        (admins if assignment.role == UserRole.ADMIN.value else sub_admins).append(item)

    return {
        "id": school.id,
        "school_name": school.school_name,
        "branch_name": school.branch_name,
        "school_code": school.school_code,
        "primary_email": school.primary_email,
        "primary_number": school.primary_number,
        "city": school.city,
        "state": school.state,
        "country": school.country,
        "session_name": school.session_name,
        "session_start_date": school.session_start_date.isoformat(),
        "session_end_date": school.session_end_date.isoformat(),
        "admins": admins,
        "sub_admins": sub_admins,
        "services": [permission.service_name for permission in school.permissions if permission.is_enabled],
    }


@router.get("/school", status_code=status.HTTP_200_OK)
def get_schools(
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    query = db.query(School)
    if _role_value(current_user) != UserRole.SUPER_ADMIN.value:
        query = query.join(SchoolUserAssignment).filter(SchoolUserAssignment.user_id == current_user.id)
    schools = query.all()
    return {
        "status": "success",
        "message": "Schools fetched successfully",
        "data": [_school_payload(school) for school in schools],
    }


@router.post("/create_school", status_code=status.HTTP_200_OK)
def create_school(
    payload: SchoolCreate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)

    school_info = payload.school_info
    admin_id = payload.assign_admin.admin_id
    sub_admin_ids = payload.assign_sub_admin.sub_admin_ids
    services = payload.add_services.services

    duplicate_filters = [School.primary_email == str(school_info.primary_email)]
    for field in ("school_code", "school_affiliation_no", "u_dais_code"):
        value = getattr(school_info, field)
        if value:
            duplicate_filters.append(getattr(School, field) == value)
    if db.query(School).filter(or_(*duplicate_filters)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="School code, email, affiliation number, or U-DAIS code already exists")

    user_ids = [admin_id, *sub_admin_ids]
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    users_by_id = {user.id: user for user in users}
    missing_ids = sorted(set(user_ids) - users_by_id.keys())
    if missing_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Users not found: {missing_ids}")

    admin = users_by_id[admin_id]
    if _role_value(admin) != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="admin_id must belong to a user with Admin role")
    invalid_sub_admins = [user_id for user_id in sub_admin_ids if _role_value(users_by_id[user_id]) != UserRole.SUB_ADMIN.value]
    if invalid_sub_admins:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Users are not Sub Admins: {invalid_sub_admins}")
    inactive_ids = [user.id for user in users if user.status != UserStatus.ACTIVE]
    if inactive_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Assigned users are inactive: {inactive_ids}")

    school = School(**payload.school_values())
    school.assignments.append(SchoolUserAssignment(user_id=admin_id, role=UserRole.ADMIN.value))
    school.assignments.extend(
        SchoolUserAssignment(user_id=user_id, role=UserRole.SUB_ADMIN.value)
        for user_id in sub_admin_ids
    )
    school.permissions.extend(
        SchoolPermission(service_name=service, is_enabled=True) for service in services
    )

    try:
        db.add(school)
        db.commit()
        db.refresh(school)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="School contains duplicate unique details") from exc
    except Exception:
        db.rollback()
        raise

    return {
        "status": "success",
        "message": "School created successfully",
        "data": _school_payload(school),
    }

import re

from fastapi import APIRouter, Depends, HTTPException, status
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Super Admin can manage schools")


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


def _school_detail_payload(school: School) -> dict:
    """Return every schools-table field along with related assignments and services."""
    payload = {
        column.name: getattr(school, column.name)
        for column in School.__table__.columns
    }
    for field in ("session_start_date", "session_end_date"):
        value = payload[field]
        payload[field] = value.isoformat() if value else None

    summary = _school_payload(school)
    payload["admins"] = summary["admins"]
    payload["sub_admins"] = summary["sub_admins"]
    payload["services"] = summary["services"]
    return payload


def _duplicate_school_fields(db: Session, school_info, exclude_school_id: int | None = None) -> dict[str, str]:
    unique_fields = {
        "primary_email": str(school_info.primary_email),
        "school_code": school_info.school_code,
        "school_affiliation_no": school_info.school_affiliation_no,
        "u_dais_code": school_info.u_dais_code,
    }
    duplicates = {}
    for field, value in unique_fields.items():
        normalized_value = value.strip() if isinstance(value, str) else value
        query = db.query(School.id).filter(getattr(School, field) == normalized_value)
        if exclude_school_id is not None:
            query = query.filter(School.id != exclude_school_id)
        if normalized_value and query.first():
            duplicates[field] = normalized_value
    return duplicates


def _duplicate_school_detail(duplicates: dict[str, str]) -> dict:
    return {
        "message": "School contains duplicate unique details",
        "fields": duplicates,
    }


def _integrity_error_detail(exc: IntegrityError, school_info) -> dict:
    """Return the duplicate field and submitted value reported by MySQL."""
    error_message = str(getattr(exc, "orig", exc))
    match = re.search(
        r"Duplicate entry ['\"](?P<value>.*?)['\"] for key ['\"](?P<key>.*?)['\"]",
        error_message,
        flags=re.IGNORECASE,
    )
    if not match:
        return {"message": "School contains conflicting unique details"}

    constraint = match.group("key")
    constraint_name = constraint.rsplit(".", 1)[-1]
    unique_fields = (
        "primary_email",
        "school_code",
        "school_affiliation_no",
        "u_dais_code",
    )
    field = next((name for name in unique_fields if name in constraint_name), None)
    if field:
        return _duplicate_school_detail({field: str(getattr(school_info, field))})

    return {
        "message": "School contains conflicting unique details",
        "constraint": constraint,
        "value": match.group("value"),
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


@router.get("/school/{school_id}", status_code=status.HTTP_200_OK)
def get_school_by_id(
    school_id: int,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    query = db.query(School).filter(School.id == school_id)
    if _role_value(current_user) != UserRole.SUPER_ADMIN.value:
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id
        )

    school = query.first()
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School not found",
        )

    return {
        "status": "success",
        "message": "School fetched successfully",
        "data": _school_detail_payload(school),
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

    duplicates = _duplicate_school_fields(db, school_info)
    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_school_detail(duplicates),
        )

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
        duplicates = _duplicate_school_fields(db, school_info)
        detail = _duplicate_school_detail(duplicates) if duplicates else _integrity_error_detail(exc, school_info)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    except Exception:
        db.rollback()
        raise

    return {
        "status": "success",
        "message": "School created successfully",
        "data": _school_payload(school),
    }


@router.put("/update_school/{school_id}", status_code=status.HTTP_200_OK)
def update_school(
    school_id: int,
    payload: SchoolCreate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)

    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="School not found")

    school_info = payload.school_info
    duplicates = _duplicate_school_fields(db, school_info, exclude_school_id=school_id)
    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_school_detail(duplicates),
        )

    admin_id = payload.assign_admin.admin_id
    sub_admin_ids = payload.assign_sub_admin.sub_admin_ids
    user_ids = [admin_id, *sub_admin_ids]
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    users_by_id = {user.id: user for user in users}
    missing_ids = sorted(set(user_ids) - users_by_id.keys())
    if missing_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Users not found: {missing_ids}")
    if _role_value(users_by_id[admin_id]) != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="admin_id must belong to a user with Admin role")
    invalid_sub_admins = [user_id for user_id in sub_admin_ids if _role_value(users_by_id[user_id]) != UserRole.SUB_ADMIN.value]
    if invalid_sub_admins:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Users are not Sub Admins: {invalid_sub_admins}")
    inactive_ids = [user.id for user in users if user.status != UserStatus.ACTIVE]
    if inactive_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Assigned users are inactive: {inactive_ids}")

    for field, value in payload.school_values().items():
        setattr(school, field, value)
    desired_assignments = {
        admin_id: UserRole.ADMIN.value,
        **{user_id: UserRole.SUB_ADMIN.value for user_id in sub_admin_ids},
    }
    existing_assignments = {
        assignment.user_id: assignment
        for assignment in school.assignments
    }
    for user_id, assignment in list(existing_assignments.items()):
        if user_id not in desired_assignments:
            school.assignments.remove(assignment)
        else:
            assignment.role = desired_assignments[user_id]
    for user_id, role in desired_assignments.items():
        if user_id not in existing_assignments:
            school.assignments.append(
                SchoolUserAssignment(user_id=user_id, role=role)
            )

    try:
        # Permissions are a complete replacement during an update. Flush the
        # deletions first so the unique constraint does not conflict when a
        # previously selected service is inserted again.
        school.permissions.clear()
        db.flush()
        school.permissions.extend(
            SchoolPermission(service_name=service_name, is_enabled=True)
            for service_name in payload.add_services.services
        )
        db.commit()
        db.refresh(school)
    except IntegrityError as exc:
        db.rollback()
        duplicates = _duplicate_school_fields(db, school_info, exclude_school_id=school_id)
        detail = _duplicate_school_detail(duplicates) if duplicates else _integrity_error_detail(exc, school_info)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    except Exception:
        db.rollback()
        raise

    return {
        "status": "success",
        "message": "School updated successfully",
        "data": _school_payload(school),
    }


@router.delete("/delete_school/{school_id}", status_code=status.HTTP_200_OK)
def delete_school(
    school_id: int,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)

    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="School not found")

    try:
        db.delete(school)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "status": "success",
        "message": "School deleted successfully",
        "data": {"id": school_id},
    }

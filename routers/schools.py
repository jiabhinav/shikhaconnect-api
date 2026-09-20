from datetime import date
from utils.dates import today as get_today

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from database.module_names import get_module_names
from database.session_table import ensure_session_table, update_school_session, validate_session_years
from models.school import School, SchoolPermission
from models.school_assets import SchoolAssets
from models.user import User, UserRole
from schemas.school import SchoolCreate, SchoolUpdate
from schemas.school_assets import SchoolAssetsResponse
from schemas.session import SessionResponse
from models.session import Session as SchoolSession
from models.student import Student
from schemas.session import SessionCreate, SessionResult, SessionListResult
from routers.class_sections import router as class_section_router
from routers.subjects import router as subject_router
from routers.streams import router as stream_router
from routers.fee_categories import router as fee_category_router
from routers.caste_categories import router as caste_category_router
from routers.houses import router as house_router
from routers.generation_settings import router as generation_settings_router
from routers.timetable_settings import router as timetable_settings_router
from routers.students import router as student_router

router = APIRouter(
    prefix="/schools",
    tags=["Schools"],
)
router.include_router(class_section_router)
router.include_router(subject_router)
router.include_router(stream_router)
router.include_router(fee_category_router)
router.include_router(caste_category_router)
router.include_router(house_router)
router.include_router(generation_settings_router)
router.include_router(timetable_settings_router)
router.include_router(student_router)


def _require_session_school(db: Session, school_id: int, user: User):
    query = db.query(School).filter(School.id == school_id)
    # if _role_value(user) != UserRole.SUPER_ADMIN.value:
    #     raise HTTPException(status_code=403, detail="Only Super Admin can access schools")
    if not query.first():
        raise HTTPException(status_code=404, detail="School not found")
    try:
        ensure_session_table(db.connection())
    except SQLAlchemyError as exc:
        db.rollback()
        logging.getLogger(__name__).exception("Could not initialize the sessions table")
        raise HTTPException(
            status_code=503,
            detail="Session storage is unavailable. Check database connectivity and schema permissions.",
        ) from exc


def _save_session(db, session):
    try:
        validate_session_years(db, session.school_id, session.start_date, session.end_date, session.id)
        db.add(session)
        db.commit()
        db.refresh(session)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Session conflicts with existing database records") from exc
    except Exception:
        db.rollback()
        raise


@router.post("/school/{school_id}/sessions", response_model=SessionResult, status_code=201)
def create_session(
    school_id: int,
    payload: SessionCreate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_session_school(db, school_id, current_user)
    session = SchoolSession(school_id=school_id, **payload.model_dump())
    _save_session(db, session)
    return {"message": "Session created successfully", "data": session}


@router.put("/school/{school_id}/sessions/{session_id}", response_model=SessionResult)
def update_session(
    school_id: int,
    session_id: int,
    payload: SessionCreate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_session_school(db, school_id, current_user)
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    for field, value in payload.model_dump().items():
        setattr(session, field, value)
    _save_session(db, session)
    return {"message": "Session updated successfully", "data": session}


@router.delete("/school/{school_id}/sessions/{session_id}")
def delete_session(
    school_id: int,
    session_id: int,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_session_school(db, school_id, current_user)
    session = db.query(SchoolSession).filter_by(
        id=session_id, school_id=school_id
    ).with_for_update().first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    assigned_message = "Session already assigned to a student. You cannot delete this session."
    if db.query(Student.id).filter(Student.session_id == session_id).first() is not None:
        raise HTTPException(status_code=409, detail=assigned_message)

    try:
        db.delete(session)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=assigned_message) from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Session deleted successfully", "data": {"id": session_id}}


@router.get("/school/{school_id}/sessions", response_model=SessionListResult)
def list_sessions(
    school_id: int,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_session_school(db, school_id, current_user)
    sessions = db.query(SchoolSession).filter_by(school_id=school_id).order_by(
        SchoolSession.start_date.desc(), SchoolSession.id.desc()
    ).all()
    return {"message": "Sessions fetched successfully", "data": sessions}

def _role_value(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else str(user.role)


def _require_super_admin(user: User) -> None:
    if _role_value(user) != UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Super Admin can manage schools")


def _school_payload(school: School, db: Session) -> dict:
    """Return all school columns, including null fields, and permissions."""
    payload = {
        column.name: getattr(school, column.name)
        for column in School.__table__.columns
    }
    for field in ("session_start_date", "session_end_date"):
        value = payload[field]
        payload[field] = value.isoformat() if value else None
    payload["services"] = [
        permission.module_id for permission in school.permissions if permission.is_enabled
    ]
    module_names = get_module_names(db, [permission.module_id for permission in school.permissions])
    payload["permissions"] = [
        {
            "id": permission.id,
            "school_id": permission.school_id,
            "module_id": permission.module_id,
            "name": module_names.get(permission.module_id),
            "is_enabled": permission.is_enabled,
        }
        for permission in sorted(school.permissions, key=lambda item: item.id)
    ]
    return payload


def _school_detail_payload(school: School, db: Session) -> dict:
    payload = _school_payload(school, db)
    today = get_today()
    year = today.year
    session = db.query(SchoolSession).filter(
        SchoolSession.school_id == school.id,
        SchoolSession.start_date <= date(year, 12, 31),
        SchoolSession.end_date >= date(year, 1, 1),
    ).order_by(
        ((SchoolSession.start_date <= today) & (SchoolSession.end_date >= today)).desc(),
        SchoolSession.start_date.desc(), SchoolSession.id.desc(),
    ).first()
    payload["sessions"] = (
        SessionResponse.model_validate(session).model_dump(mode="json")
        if session is not None else None
    )
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
    """Extract conflicting fields and values from database integrity errors."""
    error_message = str(getattr(exc, "orig", exc))
    match = re.search(
        r"Duplicate entry ['\"](?P<value>.*?)['\"] for key ['\"](?P<key>.*?)['\"]",
        error_message,
        flags=re.IGNORECASE,
    )
    if not match:
        # PostgreSQL exposes the conflicting columns and values in DETAIL.
        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        detail = getattr(diagnostic, "message_detail", None) or error_message
        postgres_match = re.search(
            r"Key \((?P<fields>.*?)\)=\((?P<value>.*?)\) already exists",
            detail,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if postgres_match:
            fields = [field.strip().strip('"') for field in postgres_match.group("fields").split(",")]
            value = postgres_match.group("value")
            result = {
                "message": "School contains conflicting unique details",
                "field": ", ".join(fields),
                "value": value,
            }
            if len(fields) == 1:
                result["fields"] = {fields[0]: value}
            constraint = getattr(diagnostic, "constraint_name", None)
            if constraint:
                result["constraint"] = constraint
            return result

        sqlite_match = re.search(r"UNIQUE constraint failed: (?P<fields>[^\n]+)", error_message)
        if sqlite_match:
            fields = [field.strip().rsplit(".", 1)[-1] for field in sqlite_match.group("fields").split(",")]
            values = {
                field: str(getattr(school_info, field))
                for field in fields if hasattr(school_info, field)
            }
            if values:
                return _duplicate_school_detail(values)

        original = getattr(exc, "orig", None)
        code = getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)
        column = getattr(diagnostic, "column_name", None)
        messages = {
            "23502": "A required database field is missing",
            "23503": "A referenced database record does not exist",
            "23514": "A database validation constraint failed",
            "23505": "School contains conflicting unique details",
        }
        result = {
            "message": messages.get(code, "School could not be saved because a database constraint failed"),
            "table": getattr(diagnostic, "table_name", None),
            "field": column,
            "constraint": getattr(diagnostic, "constraint_name", None),
            "value": None,
        }
        if code:
            result["code"] = code
        return result

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
        raise HTTPException(status_code=403, detail="Only Super Admin can access schools")
    schools = query.all()
    return {
        "status": "success",
        "message": "Schools fetched successfully",
        "data": [_school_detail_payload(school, db) for school in schools],
    }


@router.get("/school/{school_id}", status_code=status.HTTP_200_OK)
def get_school_by_id(
    school_id: int,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    query = db.query(School).filter(School.id == school_id)
    # if _role_value(current_user) != UserRole.SUPER_ADMIN.value:
    #     raise HTTPException(status_code=403, detail="Only Super Admin can access schools")

    school = query.first()
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School not found",
        )

    data = _school_detail_payload(school, db)
    assets = db.get(SchoolAssets, school_id)
    data["school_assets"] = (
        SchoolAssetsResponse.model_validate(assets)
        if assets is not None else SchoolAssetsResponse(school_id=school_id)
    ).model_dump(mode="json")
    return {
        "status": "success",
        "message": "School fetched successfully",
        "data": data,
    }


@router.post("/create_school", status_code=status.HTTP_200_OK)
def create_school(
    payload: SchoolCreate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)

    school_info = payload.school_info
    services = [str(service) for service in payload.services]

    duplicates = _duplicate_school_fields(db, school_info)
    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_school_detail(duplicates),
        )

    school = School(**payload.school_values())
    school.permissions.extend(
        SchoolPermission(module_id=service, is_enabled=True) for service in services
    )

    try:
        ensure_session_table(db.connection())
        db.add(school)
        db.flush()
        validate_session_years(db, school.id, school_info.session_start_date, school_info.session_end_date)
        db.add(SchoolSession(
            school_id=school.id,
            name=school_info.session_name,
            start_date=school_info.session_start_date,
            end_date=school_info.session_end_date,
        ))
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
        "data": _school_payload(school, db),
    }


@router.put("/update_school/{school_id}", status_code=status.HTTP_200_OK)
def update_school(
    school_id: int,
    payload: SchoolUpdate,
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

    try:
        update_school_session(db, school, school_info, payload.session_id)
        for field, value in payload.school_values().items():
            setattr(school, field, value)

        # Permissions are a complete replacement during an update. Flush the
        # deletions first so the unique constraint does not conflict when a
        # previously selected service is inserted again.
        school.permissions.clear()
        db.flush()
        school.permissions.extend(
            SchoolPermission(module_id=int(service_name), is_enabled=True)
            for service_name in payload.services
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
        "data": _school_payload(school, db),
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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import SchoolUserAssignment
from models.session import Session as SchoolSession
from models.user import User, UserRole
from models.timetable_settings import TimetableSettings
from schemas.timetable_settings import TimetableSettingsWrite, TimetableSettingsResult

router = APIRouter(prefix="/school/{school_id}/sessions/{session_id}")


def settings_session(school_id: int, session_id: int, db: Session = Depends(get_db_session),
                     current_user: User = Depends(get_current_user)):
    query = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage timetable settings")
        query = query.join(SchoolUserAssignment, SchoolUserAssignment.school_id == SchoolSession.school_id).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School session not found")
    return db


def save_settings(db, model, session_id, payload, *, create_only=False):
    try:
        # Serialize saves, including two concurrent first saves for a session.
        db.query(SchoolSession).filter_by(id=session_id).with_for_update().one()
        item = db.query(model).filter_by(session_id=session_id).first()
        if create_only and item is not None:
            raise HTTPException(409, "Timetable settings already exist for this session; use PUT to update")
        if item is None:
            item = model(session_id=session_id)
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Settings conflict with the school session or database constraints") from exc
    except Exception:
        db.rollback()
        raise
    message = "Timetable settings created successfully" if create_only else "Timetable settings saved successfully"
    return {"message": message, "data": item}


def read_settings(db, model, session_id):
    item = db.query(model).filter_by(session_id=session_id).first()
    if item is None:
        raise HTTPException(404, "Timetable settings have not been saved for this session")
    return {"message": "Timetable settings fetched successfully", "data": item}


@router.post("/timetable-settings", response_model=TimetableSettingsResult, status_code=201)
def create_timetable_settings(session_id: int, payload: TimetableSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, TimetableSettings, session_id, payload, create_only=True)


@router.put("/timetable-settings", response_model=TimetableSettingsResult)
def save_timetable_settings(session_id: int, payload: TimetableSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, TimetableSettings, session_id, payload)


@router.get("/timetable-settings", response_model=TimetableSettingsResult)
def get_timetable_settings(session_id: int, db: Session = Depends(settings_session)):
    return read_settings(db, TimetableSettings, session_id)

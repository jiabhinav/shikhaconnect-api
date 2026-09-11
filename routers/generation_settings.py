from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import SchoolUserAssignment
from models.session import Session as SchoolSession
from models.user import User, UserRole
from models.generation_settings import FeeGenerationSettings, TransportGenerationSettings
from schemas.generation_settings import FeeSettingsWrite, TransportSettingsWrite, FeeSettingsResult, TransportSettingsResult

router = APIRouter(prefix="/school/{school_id}/sessions/{session_id}")


def settings_session(school_id: int, session_id: int, db: Session = Depends(get_db_session),
                     current_user: User = Depends(get_current_user)):
    query = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage generation settings")
        query = query.join(SchoolUserAssignment, SchoolUserAssignment.school_id == SchoolSession.school_id).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School session not found")
    return db


def save_settings(db, model, session_id, payload):
    try:
        # Serialize saves, including two concurrent first saves for a session.
        db.query(SchoolSession).filter_by(id=session_id).with_for_update().one()
        item = db.query(model).filter_by(session_id=session_id).first()
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
    return {"message": "Generation settings saved successfully", "data": item}


def read_settings(db, model, session_id):
    item = db.query(model).filter_by(session_id=session_id).first()
    if item is None:
        raise HTTPException(404, "Generation settings have not been saved for this session")
    return {"message": "Generation settings fetched successfully", "data": item}


@router.put("/fee-generation-settings", response_model=FeeSettingsResult)
def save_fee_settings(session_id: int, payload: FeeSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, FeeGenerationSettings, session_id, payload)


@router.get("/fee-generation-settings", response_model=FeeSettingsResult)
def get_fee_settings(session_id: int, db: Session = Depends(settings_session)):
    return read_settings(db, FeeGenerationSettings, session_id)


@router.put("/transport-generation-settings", response_model=TransportSettingsResult)
def save_transport_settings(session_id: int, payload: TransportSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, TransportGenerationSettings, session_id, payload)


@router.get("/transport-generation-settings", response_model=TransportSettingsResult)
def get_transport_settings(session_id: int, db: Session = Depends(settings_session)):
    return read_settings(db, TransportGenerationSettings, session_id)

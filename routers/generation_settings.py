from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.session import Session as SchoolSession
from models.user import User, UserRole
from models.generation_settings import FeeGenerationSettings, TransportGenerationSettings
from schemas.generation_settings import FeeSettingsWrite, TransportSettingsWrite, FeeSettingsResult, TransportSettingsResult

router = APIRouter()


def settings_session(school_id: int, session_id: int, db: Session = Depends(get_db_session),
                     current_user: User = Depends(get_current_user)):
    query = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id)
    # if current_user.role != UserRole.SUPER_ADMIN:
    #     raise HTTPException(status_code=403, detail="Only Super Admin can access schools")
    if query.first() is None:
        raise HTTPException(404, "School session not found")
    return db


def save_settings(db, model, school_id, session_id, payload, *, create):
    if payload.school_id is not None and payload.school_id != school_id:
        raise HTTPException(422, "Body school_id must match the school_id in the request URL")
    try:
        # Serialize writes for this session.
        session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).with_for_update().first()
        if session is None:
            raise HTTPException(404, "School session not found")
        item = db.query(model).filter_by(school_id=school_id, session_id=session_id).first()
        if create:
            if item is not None:
                raise HTTPException(409, "Generation settings already exist for this session")
            item = model(school_id=session.school_id, session_id=session_id)
        elif item is None:
            raise HTTPException(404, "Generation settings have not been saved for this session")
        for key, value in payload.model_dump(exclude={"fee_type", "school_id"}).items():
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
    action = "created" if create else "updated"
    return {"message": f"Generation settings {action} successfully", "data": item}


def read_settings(db, model, school_id, session_id):
    item = db.query(model).filter_by(school_id=school_id, session_id=session_id).first()
    if item is None:
        raise HTTPException(404, "Generation settings have not been saved for this session")
    return {"message": "Generation settings fetched successfully", "data": item}


@router.post("/fee-generation-settings", response_model=FeeSettingsResult, status_code=201)
def create_fee_settings(school_id: int, session_id: int, payload: FeeSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, FeeGenerationSettings, school_id, session_id, payload, create=True)


@router.put("/fee-generation-settings", response_model=FeeSettingsResult)
def save_fee_settings(school_id: int, session_id: int, payload: FeeSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, FeeGenerationSettings, school_id, session_id, payload, create=False)


@router.get("/fee-generation-settings", response_model=FeeSettingsResult)
def get_fee_settings(school_id: int, session_id: int, db: Session = Depends(settings_session)):
    return read_settings(db, FeeGenerationSettings, school_id, session_id)


@router.post("/transport-generation-settings", response_model=TransportSettingsResult, status_code=201)
def create_transport_settings(school_id: int, session_id: int, payload: TransportSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, TransportGenerationSettings, school_id, session_id, payload, create=True)


@router.put("/transport-generation-settings", response_model=TransportSettingsResult)
def save_transport_settings(school_id: int, session_id: int, payload: TransportSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, TransportGenerationSettings, school_id, session_id, payload, create=False)


@router.get("/transport-generation-settings", response_model=TransportSettingsResult)
def get_transport_settings(school_id: int, session_id: int, db: Session = Depends(settings_session)):
    return read_settings(db, TransportGenerationSettings, school_id, session_id)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.session import Session as SchoolSession
from models.user import User, UserRole
from models.generation_settings import FeeGenerationSettings
from schemas.enums import FeeType
from schemas.generation_settings import (
    FeeSettingsWrite,
    GenerationSettingsByFeeTypeResult,
    GenerationSettingsResult,
    TransportSettingsWrite,
)

router = APIRouter()

def settings_session(school_id: int, session_id: int, db: Session = Depends(get_db_session),
                     current_user: User = Depends(get_current_user)):
    query = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id)
    # if current_user.role != UserRole.SUPER_ADMIN:
    #     raise HTTPException(status_code=403, detail="Only Super Admin can access schools")
    if query.first() is None:
        raise HTTPException(404, "School session not found")
    return db


def save_settings(db, school_id, session_id, payload, *, create):
    if payload.school_id is not None and payload.school_id != school_id:
        raise HTTPException(422, "Body school_id must match the school_id in the request URL")
    try:
        # Serialize writes for this session.
        session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).with_for_update().first()
        if session is None:
            raise HTTPException(404, "School session not found")
        item = db.query(FeeGenerationSettings).filter_by(
            school_id=school_id, session_id=session_id, fee_type=payload.fee_type,
        ).first()
        if create:
            if item is not None:
                raise HTTPException(
                    409, f"Generation settings already exist for {payload.fee_type.value} in this session"
                )
            item = FeeGenerationSettings(school_id=session.school_id, session_id=session_id, fee_type=payload.fee_type)
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


@router.post("/fee-generation-settings", response_model=GenerationSettingsResult, status_code=201)
def create_fee_settings(school_id: int, session_id: int, payload: FeeSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, school_id, session_id, payload, create=True)


@router.put("/fee-generation-settings", response_model=GenerationSettingsResult)
def save_fee_settings(school_id: int, session_id: int, payload: FeeSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, school_id, session_id, payload, create=False)


@router.get("/fee-generation-settings", response_model=GenerationSettingsByFeeTypeResult)
@router.get("/transport-generation-settings", response_model=GenerationSettingsByFeeTypeResult)
def get_generation_settings(
    school_id: int, session_id: int, fee_type: FeeType | None = None,
    db: Session = Depends(settings_session),
):
    query = db.query(FeeGenerationSettings).filter_by(school_id=school_id, session_id=session_id)
    if fee_type is not None:
        query = query.filter_by(fee_type=fee_type)
    items = {item.fee_type.value: item for item in query.all()}
    return {"message": "Generation settings fetched successfully", "data": items}


@router.post("/transport-generation-settings", response_model=GenerationSettingsResult, status_code=201)
def create_transport_settings(school_id: int, session_id: int, payload: TransportSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, school_id, session_id, payload, create=True)


@router.put("/transport-generation-settings", response_model=GenerationSettingsResult)
def save_transport_settings(school_id: int, session_id: int, payload: TransportSettingsWrite, db: Session = Depends(settings_session)):
    return save_settings(db, school_id, session_id, payload, create=False)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School, SchoolUserAssignment
from models.stream import Stream
from models.user import User, UserRole
from schemas.stream import StreamWrite, StreamResult, StreamListResult

router = APIRouter()


def stream_school(school_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if current_user.role != UserRole.SUPER_ADMIN:
        if current_user.role not in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            raise HTTPException(403, "Only school administrators can manage streams")
        query = query.join(SchoolUserAssignment).filter(
            SchoolUserAssignment.user_id == current_user.id,
            SchoolUserAssignment.role.in_([UserRole.ADMIN.value, UserRole.SUB_ADMIN.value]),
        )
    if query.first() is None:
        raise HTTPException(404, "School not found")
    return db


def find_stream(db, school_id, stream_id):
    item = db.query(Stream).filter_by(school_id=school_id, id=stream_id).first()
    if item is None:
        raise HTTPException(404, "Stream not found")
    return item


def save_stream(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Stream name already exists for this school or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


@router.post("/school/{school_id}/streams", response_model=StreamResult, status_code=201)
def create_stream(school_id: int, payload: StreamWrite, db: Session = Depends(stream_school)):
    return save_stream(db, Stream(school_id=school_id, name=payload.name), "Stream created successfully")


@router.get("/school/{school_id}/streams", response_model=StreamListResult)
def list_streams(school_id: int, db: Session = Depends(stream_school)):
    return {"message": "Streams fetched successfully", "data": db.query(Stream).filter_by(
        school_id=school_id).order_by(Stream.id).all()}


@router.put("/school/{school_id}/streams/{stream_id}", response_model=StreamResult)
def update_stream(school_id: int, stream_id: int, payload: StreamWrite, db: Session = Depends(stream_school)):
    item = find_stream(db, school_id, stream_id)
    item.name = payload.name
    return save_stream(db, item, "Stream updated successfully")


@router.delete("/school/{school_id}/streams/{stream_id}")
def delete_stream(school_id: int, stream_id: int, db: Session = Depends(stream_school)):
    item = find_stream(db, school_id, stream_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Stream is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Stream deleted successfully", "data": {"id": stream_id}}

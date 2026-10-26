from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from dependencies.school_session import require_school_session
from models.school import School
from models.fee_head import FeeHead
from models.user import User
from schemas.fee_head import FeeHeadWrite, FeeHeadResult, FeeHeadListResult

router = APIRouter(tags=["Configure Fees"])


def fee_head_school(school_id: int, session_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if query.first() is None:
        raise HTTPException(404, "School not found")
    require_school_session(db, school_id, session_id)
    return db


def find_fee_head(db, school_id, session_id, fee_head_id):
    item = db.query(FeeHead).filter_by(school_id=school_id, session_id=session_id, id=fee_head_id).first()
    if item is None:
        raise HTTPException(404, "Fee head not found")
    return item


def save_fee_head(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee head already exists for this school session and student type or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


@router.post("/school/{school_id}/sessions/{session_id}/fee_heads", response_model=FeeHeadResult, status_code=201, include_in_schema=False)
@router.post("/{school_id}/sessions/{session_id}/fee_heads", response_model=FeeHeadResult, status_code=201, include_in_schema=False)
@router.post("/fee_heads", response_model=FeeHeadResult, status_code=201)
def create_fee_head(school_id: int, session_id: int, payload: FeeHeadWrite, db: Session = Depends(fee_head_school)):
    return save_fee_head(db, FeeHead(school_id=school_id, session_id=session_id, fee_head_name=payload.fee_head_name, student_type=payload.student_type), "Fee head created successfully")


@router.get("/school/{school_id}/sessions/{session_id}/fee_heads", response_model=FeeHeadListResult, include_in_schema=False)
@router.get("/{school_id}/sessions/{session_id}/fee_heads", response_model=FeeHeadListResult, include_in_schema=False)
@router.get("/fee_heads", response_model=FeeHeadListResult)
def list_fee_heads(school_id: int, session_id: int, db: Session = Depends(fee_head_school)):
    return {"message": "Fee heads fetched successfully", "data": db.query(FeeHead).filter_by(
        school_id=school_id, session_id=session_id).order_by(FeeHead.id).all()}


@router.get("/school/{school_id}/sessions/{session_id}/fee_heads/{fee_head_id}", response_model=FeeHeadResult, include_in_schema=False)
@router.get("/{school_id}/sessions/{session_id}/fee_heads/{fee_head_id}", response_model=FeeHeadResult, include_in_schema=False)
@router.get("/fee_heads/{fee_head_id}", response_model=FeeHeadResult)
def get_fee_head(school_id: int, session_id: int, fee_head_id: int, db: Session = Depends(fee_head_school)):
    return {"message": "Fee head fetched successfully", "data": find_fee_head(db, school_id, session_id, fee_head_id)}


@router.put("/school/{school_id}/sessions/{session_id}/fee_heads/{fee_head_id}", response_model=FeeHeadResult, include_in_schema=False)
@router.put("/{school_id}/sessions/{session_id}/fee_heads/{fee_head_id}", response_model=FeeHeadResult, include_in_schema=False)
@router.put("/fee_heads/{fee_head_id}", response_model=FeeHeadResult)
def update_fee_head(school_id: int, session_id: int, fee_head_id: int, payload: FeeHeadWrite, db: Session = Depends(fee_head_school)):
    item = find_fee_head(db, school_id, session_id, fee_head_id)
    item.fee_head_name = payload.fee_head_name
    item.student_type = payload.student_type
    return save_fee_head(db, item, "Fee head updated successfully")


@router.delete("/school/{school_id}/sessions/{session_id}/fee_heads/{fee_head_id}", include_in_schema=False)
@router.delete("/{school_id}/sessions/{session_id}/fee_heads/{fee_head_id}", include_in_schema=False)
@router.delete("/fee_heads/{fee_head_id}")
def delete_fee_head(school_id: int, session_id: int, fee_head_id: int, db: Session = Depends(fee_head_school)):
    item = find_fee_head(db, school_id, session_id, fee_head_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee head is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Fee head deleted successfully", "data": {"id": fee_head_id}}

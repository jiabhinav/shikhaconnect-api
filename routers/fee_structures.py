from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from dependencies.school_session import require_school_session
from models.school import School
from models.fee_structure import FeeStructure
from models.user import User
from schemas.fee_structure import FeeStructureWrite, FeeStructureResult, FeeStructureListResult

router = APIRouter(tags=["Configure Fees"])


def fee_structure_school(school_id: int, session_id: int, db: Session = Depends(get_db_session),
                  current_user: User = Depends(get_current_user)):
    query = db.query(School).filter(School.id == school_id)
    if query.first() is None:
        raise HTTPException(404, "School not found")
    require_school_session(db, school_id, session_id)
    return db


def find_fee_structure(db, school_id, session_id, fee_structure_id):
    item = db.query(FeeStructure).filter_by(school_id=school_id, session_id=session_id, id=fee_structure_id).first()
    if item is None:
        raise HTTPException(404, "Fee structure not found")
    return item


def save_fee_structure(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee structure already exists for this school session or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


@router.post("/school/{school_id}/sessions/{session_id}/fee_structures", response_model=FeeStructureResult, status_code=201, include_in_schema=False)
@router.post("/{school_id}/sessions/{session_id}/fee_structures", response_model=FeeStructureResult, status_code=201, include_in_schema=False)
@router.post("/fee_structures", response_model=FeeStructureResult, status_code=201)
def create_fee_structure(school_id: int, session_id: int, payload: FeeStructureWrite, db: Session = Depends(fee_structure_school)):
    return save_fee_structure(db, FeeStructure(school_id=school_id, session_id=session_id, fee_structure_name=payload.fee_structure_name), "Fee structure created successfully")


@router.get("/school/{school_id}/sessions/{session_id}/fee_structures", response_model=FeeStructureListResult, include_in_schema=False)
@router.get("/{school_id}/sessions/{session_id}/fee_structures", response_model=FeeStructureListResult, include_in_schema=False)
@router.get("/fee_structures", response_model=FeeStructureListResult)
def list_fee_structures(school_id: int, session_id: int, db: Session = Depends(fee_structure_school)):
    return {"message": "Fee structures fetched successfully", "data": db.query(FeeStructure).filter_by(
        school_id=school_id, session_id=session_id).order_by(FeeStructure.id).all()}


@router.get("/school/{school_id}/sessions/{session_id}/fee_structures/{fee_structure_id}", response_model=FeeStructureResult, include_in_schema=False)
@router.get("/{school_id}/sessions/{session_id}/fee_structures/{fee_structure_id}", response_model=FeeStructureResult, include_in_schema=False)
@router.get("/fee_structures/{fee_structure_id}", response_model=FeeStructureResult)
def get_fee_structure(school_id: int, session_id: int, fee_structure_id: int, db: Session = Depends(fee_structure_school)):
    return {"message": "Fee structure fetched successfully", "data": find_fee_structure(db, school_id, session_id, fee_structure_id)}


@router.put("/school/{school_id}/sessions/{session_id}/fee_structures/{fee_structure_id}", response_model=FeeStructureResult, include_in_schema=False)
@router.put("/{school_id}/sessions/{session_id}/fee_structures/{fee_structure_id}", response_model=FeeStructureResult, include_in_schema=False)
@router.put("/fee_structures/{fee_structure_id}", response_model=FeeStructureResult)
def update_fee_structure(school_id: int, session_id: int, fee_structure_id: int, payload: FeeStructureWrite, db: Session = Depends(fee_structure_school)):
    item = find_fee_structure(db, school_id, session_id, fee_structure_id)
    item.fee_structure_name = payload.fee_structure_name
    return save_fee_structure(db, item, "Fee structure updated successfully")


@router.delete("/school/{school_id}/sessions/{session_id}/fee_structures/{fee_structure_id}", include_in_schema=False)
@router.delete("/{school_id}/sessions/{session_id}/fee_structures/{fee_structure_id}", include_in_schema=False)
@router.delete("/fee_structures/{fee_structure_id}")
def delete_fee_structure(school_id: int, session_id: int, fee_structure_id: int, db: Session = Depends(fee_structure_school)):
    item = find_fee_structure(db, school_id, session_id, fee_structure_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee structure is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Fee structure deleted successfully", "data": {"id": fee_structure_id}}

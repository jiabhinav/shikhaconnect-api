from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.class_section import SchoolClass
from models.fee_assignment import FeeAssignment
from models.fee_structure import FeeStructure
from routers.fee_structures import fee_structure_school
from schemas.fee_assignment import FeeAssignmentWrite, FeeAssignmentResult, FeeAssignmentListResult

router = APIRouter(tags=['Configure Fees'])


def find_assignment(db, school_id, session_id, assignment_id):
    item = db.query(FeeAssignment).filter_by(id=assignment_id, school_id=school_id, session_id=session_id).first()
    if item is None:
        raise HTTPException(404, 'Fee assignment not found')
    return item


def save_assignment(db, item, payload):
    for model, identity, label in ((SchoolClass, payload.class_id, 'Class'), (FeeStructure, payload.fee_structure_id, 'Fee structure')):
        if db.query(model.id).filter_by(id=identity, school_id=item.school_id, session_id=item.session_id).first() is None:
            raise HTTPException(404, f'{label} not found for this school session')
    try:
        item.class_id = payload.class_id
        item.fee_structure_id = payload.fee_structure_id
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Class already has a fee structure assigned or conflicts with database constraints') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Fee structure assignment saved successfully', 'data': item}


@router.post('/fee_assignments', response_model=FeeAssignmentResult, status_code=201)
def create_assignment(school_id: int, session_id: int, payload: FeeAssignmentWrite, db: Session = Depends(fee_structure_school)):
    return save_assignment(db, FeeAssignment(school_id=school_id, session_id=session_id), payload)


@router.get('/fee_assignments', response_model=FeeAssignmentListResult | FeeAssignmentResult)
def get_assignments(school_id: int, session_id: int, assignment_id: int | None = None,
                    class_id: int | None = None, fee_structure_id: int | None = None,
                    search: str | None = None, db: Session = Depends(fee_structure_school)):
    if assignment_id is not None:
        return {'message': 'Fee assignment fetched successfully', 'data': find_assignment(db, school_id, session_id, assignment_id)}
    query = db.query(FeeAssignment).filter_by(school_id=school_id, session_id=session_id)
    if class_id is not None:
        query = query.filter_by(class_id=class_id)
    if fee_structure_id is not None:
        query = query.filter_by(fee_structure_id=fee_structure_id)
    if search and search.strip():
        pattern = '%' + search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.join(SchoolClass).join(FeeStructure).filter(
            SchoolClass.name.ilike(pattern, escape='\\') | FeeStructure.fee_structure_name.ilike(pattern, escape='\\'))
    items = query.order_by(FeeAssignment.id).all()
    return {'message': 'Fee assignments fetched successfully', 'total': len(items), 'data': items}


@router.put('/fee_assignments', response_model=FeeAssignmentResult)
def update_assignment(school_id: int, session_id: int, assignment_id: int, payload: FeeAssignmentWrite, db: Session = Depends(fee_structure_school)):
    return save_assignment(db, find_assignment(db, school_id, session_id, assignment_id), payload)


@router.delete('/fee_assignments')
def delete_assignment(school_id: int, session_id: int, assignment_id: int, db: Session = Depends(fee_structure_school)):
    item = find_assignment(db, school_id, session_id, assignment_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Fee assignment is in use and cannot be deleted') from exc
    except Exception:
        db.rollback()
        raise
    return {'status': 'success', 'message': 'Fee assignment deleted successfully', 'data': {'id': assignment_id}}

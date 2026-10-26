from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from schemas.fee_examples import FEE_CONFIGURATION_EXAMPLES, FEE_MONTH_PATCH_EXAMPLES
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.fee_category import FeeCategory
from models.fee_configuration import FeeConfiguration, FeeConfigurationHead
from models.fee_head import FeeHead
from models.fee_structure import FeeStructure
from models.session import Session as SchoolSession
from routers.fee_structures import fee_structure_school
from schemas.fee_configuration import (FeeConfigurationWrite, FeeConfigurationResult,
                                       FeeConfigurationListResult, FeeMonthPatch, FeeScheduleResult)

from utils.fee_months import monthly_fees, session_months

router = APIRouter(tags=["Configure Fees"])


def find_configuration(db, school_id, session_id, configuration_id):
    item = db.query(FeeConfiguration).filter_by(id=configuration_id, school_id=school_id, session_id=session_id).first()
    if item is None:
        raise HTTPException(404, "Fee configuration not found")
    return item


def validate_references(db, school_id, session_id, payload):
    for model, identity, label in (
        (FeeStructure, payload.fee_structure_id, "Fee structure"),
        (FeeCategory, payload.fee_category_id, "Fee category"),
    ):
        if db.query(model.id).filter_by(id=identity, school_id=school_id, session_id=session_id).first() is None:
            raise HTTPException(404, f"{label} not found for this school session")
    ids = [head.fee_head_id for head in payload.fee_heads]
    if db.query(FeeHead.id).filter(FeeHead.school_id == school_id, FeeHead.session_id == session_id, FeeHead.id.in_(ids)).count() != len(ids):
        raise HTTPException(404, "Fee head not found for this school session")
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).one()
    first, last = session.start_date.strftime("%Y-%m"), session.end_date.strftime("%Y-%m")
    if any(not first <= month <= last for head in payload.fee_heads for month in head.month_amounts):
        raise HTTPException(422, "Month overrides must fall within the selected session")
    required = set(session_months(session.start_date, session.end_date))
    for head in payload.fee_heads:
        if head.edit_months_wise and set(head.month_amounts) != required:
            raise HTTPException(422, {"message": "Provide values for every session month when edit_months_wise is true",
                                      "fee_head_id": head.fee_head_id,
                                      "missing_months": sorted(required - set(head.month_amounts))})
        if not head.edit_months_wise:
            head.month_amounts = {}



def write_configuration(db, item, payload):
    session = db.query(SchoolSession).filter_by(id=item.session_id, school_id=item.school_id).one()
    try:
        item.fee_structure_id = payload.fee_structure_id
        item.fee_category_id = payload.fee_category_id
        # Remove old rows before inserting replacements with the same head IDs.
        item.fee_heads.clear()
        db.add(item)
        db.flush()
        item.fee_heads = [FeeConfigurationHead(
            fee_head_id=head.fee_head_id, amount=head.amount,
            month_amounts=head.model_dump(mode="json")["month_amounts"],
            **monthly_fees(head.amount, head.model_dump(mode="json")["month_amounts"], session.start_date, session.end_date),
        ) for head in payload.fee_heads]
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Configuration already exists for this structure and category or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": "Fee configuration saved successfully", "data": item}


@router.post("/fee_configurations", response_model=FeeConfigurationResult, status_code=201)
def create_configuration(school_id: int, session_id: int, payload: Annotated[FeeConfigurationWrite, Body(openapi_examples=FEE_CONFIGURATION_EXAMPLES)], db: Session = Depends(fee_structure_school)):
    validate_references(db, school_id, session_id, payload)
    return write_configuration(db, FeeConfiguration(school_id=school_id, session_id=session_id), payload)


@router.get("/fee_configurations", response_model=FeeConfigurationListResult | FeeConfigurationResult)
def list_configurations(school_id: int, session_id: int, fee_structure_id: int | None = None, fee_category_id: int | None = None, configuration_id: int | None = None, db: Session = Depends(fee_structure_school)):
    if configuration_id is not None:
        return {"message": "Fee configuration fetched successfully", "data": find_configuration(db, school_id, session_id, configuration_id)}
    query = db.query(FeeConfiguration).filter_by(school_id=school_id, session_id=session_id)
    if fee_structure_id is not None:
        query = query.filter_by(fee_structure_id=fee_structure_id)
    if fee_category_id is not None:
        query = query.filter_by(fee_category_id=fee_category_id)
    return {"message": "Fee configurations fetched successfully", "data": query.order_by(FeeConfiguration.id).all()}


@router.get('/fee_configurations/{configuration_id}/months', response_model=FeeScheduleResult, include_in_schema=False)
@router.get('/fee_configurations/months', response_model=FeeScheduleResult)
def get_month_schedule(school_id: int, session_id: int, configuration_id: int, db: Session = Depends(fee_structure_school)):
    item = find_configuration(db, school_id, session_id, configuration_id)
    return {'message': 'Monthly fee schedule fetched successfully', 'data': configuration_schedule(db, item)}


@router.get("/fee_configurations/{configuration_id}", response_model=FeeConfigurationResult, include_in_schema=False)
def get_configuration(school_id: int, session_id: int, configuration_id: int, db: Session = Depends(fee_structure_school)):
    return {"message": "Fee configuration fetched successfully", "data": find_configuration(db, school_id, session_id, configuration_id)}


@router.put("/fee_configurations/{configuration_id}", response_model=FeeConfigurationResult, include_in_schema=False)
@router.put("/fee_configurations", response_model=FeeConfigurationResult)
def update_configuration(school_id: int, session_id: int, configuration_id: int, payload: Annotated[FeeConfigurationWrite, Body(openapi_examples=FEE_CONFIGURATION_EXAMPLES)], db: Session = Depends(fee_structure_school)):
    item = find_configuration(db, school_id, session_id, configuration_id)
    validate_references(db, school_id, session_id, payload)
    return write_configuration(db, item, payload)


@router.delete("/fee_configurations/{configuration_id}", include_in_schema=False)
@router.delete("/fee_configurations")
def delete_configuration(school_id: int, session_id: int, configuration_id: int, db: Session = Depends(fee_structure_school)):
    item = find_configuration(db, school_id, session_id, configuration_id)
    try:
        db.delete(item)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee configuration is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Fee configuration deleted successfully", "data": {"id": configuration_id}}


def configuration_schedule(db, item):
    from decimal import Decimal
    from utils.fee_months import month_schedule, session_months

    session = db.query(SchoolSession).filter_by(id=item.session_id, school_id=item.school_id).one()
    totals = {month: Decimal('0.00') for month in session_months(session.start_date, session.end_date)}
    names = dict(db.query(FeeHead.id, FeeHead.fee_head_name).filter(FeeHead.id.in_([head.fee_head_id for head in item.fee_heads])).all())
    heads = []
    for head in item.fee_heads:
        months = month_schedule(head.amount, head.month_amounts, session.start_date, session.end_date)
        for row in months:
            totals[row['month']] += row['effective_amount']
        heads.append({'fee_head_id': head.fee_head_id, 'fee_head_name': names[head.fee_head_id],
                      'amount': head.amount, 'months': months,
                      'total': sum((row['effective_amount'] for row in months), Decimal('0.00'))})
    return {'configuration_id': item.id, 'school_id': item.school_id, 'session_id': item.session_id,
            'fee_heads': heads, 'monthly_totals': totals, 'total': sum(totals.values(), Decimal('0.00'))}


@router.patch('/fee_configurations/{configuration_id}/fee_heads/{fee_head_id}/months', response_model=FeeScheduleResult, include_in_schema=False)
@router.patch('/fee_configurations/fee_heads/months', response_model=FeeScheduleResult)
def patch_month_schedule(school_id: int, session_id: int, configuration_id: int, fee_head_id: int,
                         payload: Annotated[FeeMonthPatch, Body(openapi_examples=FEE_MONTH_PATCH_EXAMPLES)], db: Session = Depends(fee_structure_school)):
    # Serialize edits of a configuration so concurrent month changes are merged.
    item = db.query(FeeConfiguration).filter_by(id=configuration_id, school_id=school_id, session_id=session_id).with_for_update().first()
    if item is None:
        raise HTTPException(404, 'Fee configuration not found')
    head = next((head for head in item.fee_heads if head.fee_head_id == fee_head_id), None)
    if head is None:
        raise HTTPException(404, 'Fee head not found in configuration')
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).one()
    first, last = session.start_date.strftime('%Y-%m'), session.end_date.strftime('%Y-%m')
    if any(not first <= month <= last for month in payload.month_amounts):
        raise HTTPException(422, 'Month changes must fall within the selected session')
    try:
        overrides = dict(head.month_amounts)
        for month, settings in payload.month_amounts.items():
            if settings is None:
                overrides.pop(month, None)
            else:
                overrides[month] = settings.model_dump(mode='json')
        if payload.amount is not None:
            head.amount = payload.amount
        head.month_amounts = overrides
        for column, value in monthly_fees(head.amount, overrides, session.start_date, session.end_date).items():
            setattr(head, column, value)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Monthly fee update conflicts with database constraints') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Monthly fees updated successfully', 'data': configuration_schedule(db, item)}

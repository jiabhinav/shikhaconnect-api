from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from schemas.fee_examples import (FEE_CONFIGURATION_EXAMPLES,
                                  FEE_CONFIGURATION_UPDATE_EXAMPLES,
                                  FEE_MONTH_PATCH_EXAMPLES)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.fee_category import FeeCategory
from models.fee_configuration import FeeConfigurationHead
from models.fee_head import FeeHead
from models.fee_structure import FeeStructure
from models.session import Session as SchoolSession
from routers.fee_structures import fee_structure_school
from schemas.fee_configuration import (FeeConfigurationWrite, FeeConfigurationUpdate,
                                       FeeConfigurationResult, FeeConfigurationListResult,
                                       FeeMonthPatch, FeeScheduleResult)

from utils.fee_months import MONTH_COLUMNS, month_schedule, session_months

router = APIRouter(tags=["Configure Fees"])


def find_configuration(db, school_id, session_id, configuration_id):
    rows = db.query(FeeConfigurationHead).filter_by(
        configuration_id=configuration_id, school_id=school_id, session_id=session_id,
    ).order_by(FeeConfigurationHead.id).all()
    if not rows:
        raise HTTPException(404, "Fee configuration not found")
    return rows


def validate_references(db, school_id, session_id, payload):
    for model, identity, label in (
        (FeeStructure, payload.fee_structure_id, "Fee structure"),
        (FeeCategory, payload.fee_category_id, "Fee category"),
    ):
        if identity is None:
            continue
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



def write_configuration(db, existing_rows, school_id, session_id, payload):
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).one()
    old = existing_rows or []
    structure_id = payload.fee_structure_id or (old[0].fee_structure_id if old else None)
    category_id = payload.fee_category_id or (old[0].fee_category_id if old else None)
    existing_group_id = old[0].configuration_id if old else None
    conflicting = db.query(FeeConfigurationHead.id).filter_by(
        school_id=school_id, session_id=session_id,
        fee_structure_id=structure_id, fee_category_id=category_id,
    )
    if existing_group_id is not None:
        conflicting = conflicting.filter(FeeConfigurationHead.configuration_id != existing_group_id)
    if conflicting.first() is not None:
        raise HTTPException(409, 'Configuration already exists for this structure and category')
    try:
        if old:
            db.query(FeeConfigurationHead).filter_by(
                configuration_id=existing_group_id, school_id=school_id, session_id=session_id,
            ).delete(synchronize_session=False)
        db.flush()
        head_rows = []
        for head in payload.fee_heads:
            overrides = head.month_amounts if head.edit_months_wise else {}
            monthly_config = []
            for month in session_months(session.start_date, session.end_date):
                value = overrides.get(month, head.amount)
                enabled = value.get("enabled", True) if isinstance(value, dict) else True
                month_amount = value["amount"] if isinstance(value, dict) else value
                monthly_config.append({
                    "month_key": month,
                    "amount": float(month_amount),
                    "enabled": enabled,
                    "is_override": value.get("is_override", month in overrides) if isinstance(value, dict) else month in overrides,
                })
            head_rows.append(FeeConfigurationHead(
                school_id=school_id,
                session_id=session_id,
                fee_structure_id=structure_id,
                fee_category_id=category_id,
                fee_head_id=head.fee_head_id,
                base_amount=head.amount,
                edit_months_wise=head.edit_months_wise,
                monthly_config=monthly_config,
            ))
        db.add(head_rows[0])
        db.flush()
        group_id = existing_group_id or head_rows[0].id
        head_rows[0].configuration_id = group_id
        for row in head_rows[1:]:
            row.configuration_id = group_id
            db.add(row)
        db.commit()
        for row in head_rows:
            db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Configuration already exists for this structure and category or conflicts with database constraints") from exc
    except Exception:
        db.rollback()
        raise
    return {"message": "Fee configuration saved successfully", "data": configuration_response(head_rows)}


def configuration_response(rows):
    """Serialize one JSON monthly configuration row per fee head."""
    from decimal import Decimal

    heads = []
    for row in rows:
        by_month = {month["month_key"]: month for month in row.monthly_config or []}
        months = {}
        for month, value in by_month.items():
            if not value.get("enabled", True):
                months[month] = {"amount": None, "enabled": False}
            elif value.get("is_override", False):
                months[month] = {"amount": value["amount"], "enabled": True}
            else:
                months[month] = value["amount"]
        legacy_months = {}
        for number, column in MONTH_COLUMNS.items():
            candidates = [month for month in by_month.values()
                          if int(month["month_key"][5:]) == number]
            value = min(candidates, key=lambda month: month["month_key"]) if candidates else None
            legacy_months[column] = (value["amount"] if value and value.get("enabled", True)
                                     else Decimal("0.00"))
        heads.append({
            "id": row.id, "fee_head_id": row.fee_head_id,
            "amount": row.base_amount,
            "edit_months_wise": row.edit_months_wise,
            "month_amounts": months, **legacy_months,
        })
    first_row = rows[0]
    return {"id": first_row.configuration_id, "school_id": first_row.school_id, "session_id": first_row.session_id,
            "fee_structure_id": first_row.fee_structure_id, "fee_category_id": first_row.fee_category_id,
            "no_of_fee_heads": len(heads), "fee_heads": heads}


@router.post("/fee_configurations", response_model=FeeConfigurationResult, status_code=201)
def create_configuration(school_id: int, session_id: int, payload: Annotated[FeeConfigurationWrite, Body(openapi_examples=FEE_CONFIGURATION_EXAMPLES)], db: Session = Depends(fee_structure_school)):
    validate_references(db, school_id, session_id, payload)
    return write_configuration(db, None, school_id, session_id, payload)


@router.get("/fee_configurations", response_model=FeeConfigurationListResult | FeeConfigurationResult)
def list_configurations(school_id: int, session_id: int, fee_structure_id: int | None = None, fee_category_id: int | None = None, configuration_id: int | None = None, db: Session = Depends(fee_structure_school)):
    if configuration_id is not None:
        return {"message": "Fee configuration fetched successfully", "data": configuration_response(find_configuration(db, school_id, session_id, configuration_id))}
    query = db.query(FeeConfigurationHead).filter_by(school_id=school_id, session_id=session_id)
    if fee_structure_id is not None:
        query = query.filter_by(fee_structure_id=fee_structure_id)
    if fee_category_id is not None:
        query = query.filter_by(fee_category_id=fee_category_id)
    rows = query.order_by(FeeConfigurationHead.configuration_id, FeeConfigurationHead.id).all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row.configuration_id, []).append(row)
    return {"message": "Fee configurations fetched successfully", "data": [configuration_response(group) for group in grouped.values()]}


@router.get('/fee_configurations/{configuration_id}/months', response_model=FeeScheduleResult, include_in_schema=False)
@router.get('/fee_configurations/months', response_model=FeeScheduleResult)
def get_month_schedule(school_id: int, session_id: int, configuration_id: int, db: Session = Depends(fee_structure_school)):
    rows = find_configuration(db, school_id, session_id, configuration_id)
    return {'message': 'Monthly fee schedule fetched successfully', 'data': configuration_schedule(db, rows)}


@router.get("/fee_configurations/{configuration_id}", response_model=FeeConfigurationResult, include_in_schema=False)
def get_configuration(school_id: int, session_id: int, configuration_id: int, db: Session = Depends(fee_structure_school)):
    return {"message": "Fee configuration fetched successfully", "data": configuration_response(find_configuration(db, school_id, session_id, configuration_id))}


@router.put("/fee_configurations/{configuration_id}", response_model=FeeConfigurationResult, include_in_schema=False)
@router.put("/fee_configurations", response_model=FeeConfigurationResult)
def update_configuration(school_id: int, session_id: int, configuration_id: int, payload: Annotated[FeeConfigurationUpdate, Body(openapi_examples=FEE_CONFIGURATION_UPDATE_EXAMPLES)], db: Session = Depends(fee_structure_school)):
    rows = find_configuration(db, school_id, session_id, configuration_id)
    validate_references(db, school_id, session_id, payload)
    return write_configuration(db, rows, school_id, session_id, payload)


@router.delete("/fee_configurations/{configuration_id}", include_in_schema=False)
@router.delete("/fee_configurations")
def delete_configuration(school_id: int, session_id: int, configuration_id: int, db: Session = Depends(fee_structure_school)):
    rows = find_configuration(db, school_id, session_id, configuration_id)
    try:
        for row in rows:
            db.delete(row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Fee configuration is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Fee configuration deleted successfully", "data": {"id": configuration_id}}


def configuration_schedule(db, rows):
    from decimal import Decimal

    first_row = rows[0]
    session = db.query(SchoolSession).filter_by(id=first_row.session_id, school_id=first_row.school_id).one()
    totals = {month: Decimal('0.00') for month in session_months(session.start_date, session.end_date)}
    names = dict(db.query(FeeHead.id, FeeHead.fee_head_name).filter(
        FeeHead.id.in_([row.fee_head_id for row in rows])).all())
    heads = []
    for row in rows:
        fee_head_id = row.fee_head_id
        overrides = {
            month['month_key']: {'amount': month['amount'], 'enabled': month.get('enabled', True)}
            for month in row.monthly_config or []
            if month.get('is_override', False) or not month.get('enabled', True)
        }
        base_amount = row.base_amount
        months = month_schedule(base_amount, overrides, session.start_date, session.end_date)
        for row in months:
            totals[row['month']] += row['effective_amount']
        heads.append({'fee_head_id': fee_head_id, 'fee_head_name': names[fee_head_id],
                      'amount': base_amount, 'months': months,
                      'total': sum((row['effective_amount'] for row in months), Decimal('0.00'))})
    return {'configuration_id': first_row.configuration_id, 'school_id': first_row.school_id,
            'session_id': first_row.session_id,
            'fee_heads': heads, 'monthly_totals': totals, 'total': sum(totals.values(), Decimal('0.00'))}


@router.patch('/fee_configurations/{configuration_id}/fee_heads/{fee_head_id}/months', response_model=FeeScheduleResult, include_in_schema=False)
@router.patch('/fee_configurations/fee_heads/months', response_model=FeeScheduleResult)
def patch_month_schedule(school_id: int, session_id: int, configuration_id: int, fee_head_id: int,
                         payload: Annotated[FeeMonthPatch, Body(openapi_examples=FEE_MONTH_PATCH_EXAMPLES)], db: Session = Depends(fee_structure_school)):
    # Serialize edits of a configuration so concurrent month changes are merged.
    rows = db.query(FeeConfigurationHead).filter_by(
        configuration_id=configuration_id, school_id=school_id, session_id=session_id,
    ).order_by(FeeConfigurationHead.id).with_for_update().all()
    if not rows:
        raise HTTPException(404, 'Fee configuration not found')
    head_row = next((row for row in rows if row.fee_head_id == fee_head_id), None)
    if head_row is None:
        raise HTTPException(404, 'Fee head not found in configuration')
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).one()
    first, last = session.start_date.strftime('%Y-%m'), session.end_date.strftime('%Y-%m')
    if any(not first <= month <= last for month in payload.month_amounts):
        raise HTTPException(422, 'Month changes must fall within the selected session')
    try:
        monthly_config = {
            month['month_key']: dict(month)
            for month in (head_row.monthly_config or [])
        }
        base_amount = head_row.base_amount
        if payload.amount is not None:
            base_amount = payload.amount
            for month in monthly_config.values():
                if not month.get('is_override', False):
                    month['amount'] = float(base_amount)
        for month, settings in payload.month_amounts.items():
            if settings is None:
                monthly_config[month] = {
                    'month_key': month, 'amount': float(base_amount),
                    'enabled': True, 'is_override': False,
                }
            else:
                monthly_config[month] = {
                    'month_key': month, 'amount': float(settings.amount),
                    'enabled': settings.enabled, 'is_override': True,
                }
        head_row.base_amount = base_amount
        head_row.monthly_config = [monthly_config[month]
                                   for month in session_months(session.start_date, session.end_date)]
        head_row.edit_months_wise = any(
            month.get('is_override', False) for month in head_row.monthly_config
        )
        db.commit()
        for row in rows:
            db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Monthly fee update conflicts with database constraints') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Monthly fees updated successfully', 'data': configuration_schedule(db, rows)}

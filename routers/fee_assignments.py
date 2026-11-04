from decimal import Decimal
from calendar import month_name

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, joinedload

from models.class_section import ClassSectionMapping, SchoolClass, Section
from models.fee_category import FeeCategory
from models.fee_assignment import FeeAssignment
from models.fee_configuration import FeeConfigurationHead
from models.fee_head import FeeHead
from models.fee_structure import FeeStructure
from models.session import Session as SchoolSession
from routers.fee_structures import fee_structure_school
from schemas.fee_assignment import (
    FeeAssignmentWrite, FeeAssignmentBatchWrite, FeeAssignmentResult, FeeAssignmentListResult,
    AssignedFeeStructureDetailsResult,
)
from utils.fee_months import session_months

router = APIRouter(tags=['Configure Fees'])


def find_assignment(db, school_id, session_id, assignment_id):
    item = db.query(FeeAssignment).filter_by(id=assignment_id, school_id=school_id, session_id=session_id).first()
    if item is None:
        raise HTTPException(404, 'Fee assignment not found')
    return item


def resolve_fee_configuration_id(db, school_id, session_id, fee_configure_id, fee_structure_id):
    if fee_configure_id is not None:
        configuration = db.query(FeeConfigurationHead).filter_by(
            id=fee_configure_id, configuration_id=fee_configure_id,
            school_id=school_id, session_id=session_id,
        ).first()
        if configuration is None:
            raise HTTPException(404, 'Fee configuration not found for this school session')
        if fee_structure_id is not None and configuration.fee_structure_id != fee_structure_id:
            raise HTTPException(422, 'fee_structure_id does not match fee_configure_id')
        return configuration.configuration_id

    configuration_ids = [row[0] for row in db.query(FeeConfigurationHead.configuration_id).filter_by(
        school_id=school_id, session_id=session_id, fee_structure_id=fee_structure_id,
    ).distinct().order_by(FeeConfigurationHead.configuration_id).all()]
    if not configuration_ids:
        raise HTTPException(404, 'Fee configuration not found for this fee structure and school session')
    # The old API accepted a structure ID. Resolve it to the first configured
    # group so existing clients do not accidentally store the structure ID.
    return configuration_ids[0]


def save_assignment(db, item, payload):
    if db.query(SchoolClass.id).filter_by(
            id=payload.class_id, school_id=item.school_id, session_id=item.session_id).first() is None:
        raise HTTPException(404, 'Class not found for this school session')
    fee_configure_id = resolve_fee_configuration_id(
        db, item.school_id, item.session_id, payload.fee_configure_id, payload.fee_structure_id,
    )
    if db.query(ClassSectionMapping.id).filter_by(
        school_id=item.school_id,
        session_id=item.session_id,
        class_id=payload.class_id,
        section_id=payload.section_id,
    ).first() is None:
        raise HTTPException(404, 'Section is not mapped to this class for this school session')
    try:
        item.class_id = payload.class_id
        item.section_id = payload.section_id
        item.fee_configure_id = fee_configure_id
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


@router.post('/fee_assignments', response_model=FeeAssignmentListResult, status_code=201)
def create_assignment(school_id: int, session_id: int, payload: FeeAssignmentBatchWrite, db: Session = Depends(fee_structure_school)):
    if db.query(SchoolClass.id).filter_by(
            id=payload.class_id, school_id=school_id, session_id=session_id).first() is None:
        raise HTTPException(404, 'Class not found for this school session')
    fee_configure_id = resolve_fee_configuration_id(
        db, school_id, session_id, payload.fee_configure_id, payload.fee_structure_id,
    )
    section_ids = list(dict.fromkeys(payload.section_id))
    mapped = {row[0] for row in db.query(ClassSectionMapping.section_id).filter_by(
        school_id=school_id, session_id=session_id, class_id=payload.class_id,
    ).filter(ClassSectionMapping.section_id.in_(section_ids)).all()}
    if mapped != set(section_ids):
        raise HTTPException(404, {'message': 'One or more sections are not mapped to this class for this school session',
                                  'section_ids': sorted(set(section_ids) - mapped)})
    items = [FeeAssignment(school_id=school_id, session_id=session_id, class_id=payload.class_id,
                           section_id=section_id, fee_configure_id=fee_configure_id)
             for section_id in section_ids]
    try:
        db.add_all(items)
        db.commit()
        for item in items:
            db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'One or more sections already have a fee structure assigned or conflict with database constraints') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Fee structure assignments saved successfully', 'total': len(items), 'data': items}


@router.get('/fee_assignments', response_model=FeeAssignmentListResult | FeeAssignmentResult)
def get_assignments(school_id: int, session_id: int, assignment_id: int | None = None,
                    class_id: int | None = None, section_id: int | None = None,
                    fee_structure_id: int | None = None, fee_configure_id: int | None = None,
                    search: str | None = None, db: Session = Depends(fee_structure_school)):
    if assignment_id is not None:
        return {'message': 'Fee assignment fetched successfully', 'data': find_assignment(db, school_id, session_id, assignment_id)}
    query = db.query(FeeAssignment).options(
        joinedload(FeeAssignment.school_class),
        joinedload(FeeAssignment.section),
        joinedload(FeeAssignment.fee_configuration).joinedload(FeeConfigurationHead.fee_structure),
        joinedload(FeeAssignment.fee_configuration).joinedload(FeeConfigurationHead.fee_category),
    ).filter_by(school_id=school_id, session_id=session_id)
    if class_id is not None:
        query = query.filter_by(class_id=class_id)
    if section_id is not None:
        query = query.filter_by(section_id=section_id)
    has_search = bool(search and search.strip())
    if fee_structure_id is not None or has_search:
        query = query.join(FeeConfigurationHead, FeeConfigurationHead.id == FeeAssignment.fee_configure_id)
    if fee_structure_id is not None:
        query = query.filter(FeeConfigurationHead.fee_structure_id == fee_structure_id)
    if fee_configure_id is not None:
        query = query.filter_by(fee_configure_id=fee_configure_id)
    if has_search:
        pattern = '%' + search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.join(SchoolClass).join(
            FeeStructure, FeeStructure.id == FeeConfigurationHead.fee_structure_id,
        ).filter(
            SchoolClass.name.ilike(pattern, escape='\\') | FeeStructure.fee_structure_name.ilike(pattern, escape='\\'))
    items = query.order_by(FeeAssignment.id).all()
    return {'message': 'Fee assignments fetched successfully', 'total': len(items), 'data': items}


@router.get('/fee_assignments/monthly-details', response_model=AssignedFeeStructureDetailsResult)
def get_assigned_fee_structure_monthly_details(
        school_id: int, session_id: int, assignment_id: int | None = None,
        db: Session = Depends(fee_structure_school)):
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).first()
    if session is None:
        raise HTTPException(404, 'School session not found')

    selected_assignment = None
    if assignment_id is not None:
        selected_assignment = find_assignment(db, school_id, session_id, assignment_id)

    months = list(session_months(session.start_date, session.end_date))
    grouped = {}
    Anchor = aliased(FeeConfigurationHead)
    ConfiguredHead = aliased(FeeConfigurationHead)
    rows_query = db.query(
        FeeAssignment, FeeStructure, Anchor, ConfiguredHead, FeeCategory, FeeHead,
        SchoolClass, Section,
    ).join(
        Anchor, Anchor.id == FeeAssignment.fee_configure_id,
    ).outerjoin(
        FeeStructure, FeeStructure.id == Anchor.fee_structure_id,
    ).outerjoin(
        ConfiguredHead, ConfiguredHead.configuration_id == Anchor.configuration_id,
    ).outerjoin(
        FeeCategory, FeeCategory.id == ConfiguredHead.fee_category_id,
    ).outerjoin(
        FeeHead, FeeHead.id == ConfiguredHead.fee_head_id,
    ).join(
        SchoolClass, SchoolClass.id == FeeAssignment.class_id,
    ).outerjoin(
        Section, Section.id == FeeAssignment.section_id,
    ).filter(
        FeeAssignment.school_id == school_id,
        FeeAssignment.session_id == session_id,
    )
    if selected_assignment is not None:
        rows_query = rows_query.filter(
            FeeAssignment.class_id == selected_assignment.class_id,
            FeeAssignment.fee_configure_id == selected_assignment.fee_configure_id,
        )
    rows = rows_query.order_by(
        FeeStructure.fee_structure_name, FeeCategory.name, FeeHead.fee_head_name,
        SchoolClass.name, Section.name,
    ).all()

    for assignment, structure, anchor, head_month, category, head, school_class, section in rows:
        category_id = category.id if category is not None else None
        key = (structure.id, category_id)
        structure_group = grouped.setdefault(key, {
            'session_name': session.name,
            'fee_structure_id': structure.id,
            'fee_structure_name': structure.fee_structure_name,
            'fee_category_id': category_id,
            'fee_category_name': category.name if category is not None else None,
            'assignments': {},
            'months': {month: {} for month in months},
        })
        structure_group['assignments'][assignment.id] = {
            'assignment_id': assignment.id,
            'class_id': assignment.class_id,
            'class_name': school_class.name,
            'section_id': assignment.section_id,
            'section_name': section.name if section is not None else None,
        }
        if head_month is not None and head is not None:
            for month_settings in head_month.monthly_config or []:
                month = month_settings.get('month_key')
                if month not in structure_group['months']:
                    continue
                enabled = month_settings.get('enabled', True)
                entered_amount = Decimal(str(month_settings.get('amount', head_month.base_amount)))
                structure_group['months'][month][head.id] = {
                    'fee_head_id': head.id,
                    'fee_head_name': head.fee_head_name,
                    'amount': entered_amount if enabled else Decimal('0.00'),
                    'entered_amount': entered_amount,
                    'enabled': enabled,
                }

    data = []
    for _, value in sorted(grouped.items(), key=lambda pair: (
            pair[1]['fee_structure_name'].casefold(), (pair[1]['fee_category_name'] or '').casefold())):
        monthly_details = []
        total_session_fee = Decimal('0.00')
        for month in months:
            fee_heads = sorted(value['months'][month].values(), key=lambda item: item['fee_head_name'].casefold())
            month_total = sum((item['amount'] for item in fee_heads), Decimal('0.00'))
            total_session_fee += month_total
            monthly_details.append({
                'month': month,
                'month_name': month_name[int(month[5:])],
                'fee_heads': fee_heads,
                'total': month_total,
            })
        data.append({
            'session_name': value['session_name'],
            'fee_structure_id': value['fee_structure_id'],
            'fee_structure_name': value['fee_structure_name'],
            'fee_category_id': value['fee_category_id'],
            'fee_category_name': value['fee_category_name'],
            'assignments': list(value['assignments'].values()),
            'months': monthly_details,
            'total_session_fee': total_session_fee,
        })
    return {
        'message': 'Assigned fee structure monthly details fetched successfully',
        'school_id': school_id,
        'session_id': session_id,
        'total_structures': len(data),
        'data': data,
    }


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

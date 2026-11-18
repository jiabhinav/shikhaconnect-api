from decimal import Decimal
from calendar import month_name
from collections import defaultdict

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
from models.student import Student
from models.student_carry_forward import CARRY_FORWARD_TAG, StudentCarryForward
from models.student_fee_adjustment import StudentFeeAdjustment
from models.student_fee import StudentFee, StudentFeeHead
from routers.fee_structures import fee_structure_school
from schemas.fee_assignment import (
    FeeAssignmentWrite, FeeAssignmentBatchWrite, FeeAssignmentResult, FeeAssignmentListResult,
    AssignedFeeStructureDetailsResult,
)
from schemas.student_fee import (
    FeePaymentAdjustmentStatus, FeePaymentHeadStatus, FeePaymentMonthStatus, FeePaymentStatusListResult,
    StudentFeeListResult, StudentFeeWrite,
)
from schemas.enums import FeeAdjustmentType, StudentFeeStatus
from utils.fee_months import session_months

router = APIRouter(tags=['Configure Fees'])


def calculate_student_fee_status(net_amount, paid_amount):
    if paid_amount >= net_amount:
        return 'PAID'
    if paid_amount == 0:
        return 'UNPAID'
    return 'PARTIAL'


@router.post('/student_fees', response_model=StudentFeeListResult, status_code=201)
def create_student_fee(school_id: int, session_id: int, payload: StudentFeeWrite,
                       db: Session = Depends(fee_structure_school)):
    student = db.query(Student.id, Student.class_id).filter_by(
        id=payload.student_id, school_id=school_id, session_id=session_id,
    ).first()
    if student is None:
        raise HTTPException(404, 'Student not found for this school session')

    assignments = db.query(FeeAssignment, FeeConfigurationHead).join(
        FeeConfigurationHead, FeeConfigurationHead.id == FeeAssignment.fee_configure_id,
    ).filter(
        FeeAssignment.school_id == school_id,
        FeeAssignment.session_id == session_id,
        FeeAssignment.class_id == student.class_id,
    ).all()
    items = []
    try:
        for month_payment in payload.months:
            existing = db.query(StudentFee).filter_by(
                school_id=school_id,
                academic_session_id=session_id,
                student_id=payload.student_id,
                fee_month=month_payment.fee_month,
            ).with_for_update().first()
            if existing is not None:
                if existing.due_amount == 0:
                    raise HTTPException(409, f'Fee for {month_payment.fee_month} is already fully paid')
                due_heads = sorted(
                    (head for head in existing.fee_heads if head.due_amount > 0),
                    key=lambda head: head.fee_head_id,
                )
                total_due = sum((head.due_amount for head in due_heads), Decimal('0.00'))
                if month_payment.payment_amount > total_due:
                    raise HTTPException(422, f'payment_amount cannot exceed the remaining due for {month_payment.fee_month}')

                remaining_payment = month_payment.payment_amount
                for head in due_heads:
                    head_paid = min(remaining_payment, head.due_amount)
                    head.paid_amount += head_paid
                    remaining_payment -= head_paid
                existing.paid_amount += month_payment.payment_amount
                existing.status = calculate_student_fee_status(existing.net_amount, existing.paid_amount)
                existing.payment_mode = payload.payment_mode
                existing.description = payload.description
                existing.collected_by = payload.collected_by
                items.append(existing)
                continue

            monthly_heads = {}
            for _, anchor in assignments:
                configured_rows = db.query(FeeConfigurationHead, FeeHead).join(
                    FeeHead, FeeHead.id == FeeConfigurationHead.fee_head_id,
                ).filter(FeeConfigurationHead.configuration_id == anchor.configuration_id).all()
                for head_config, fee_head in configured_rows:
                    month_value = next((value for value in (head_config.monthly_config or [])
                                        if value.get('month_key') == month_payment.fee_month), None)
                    if month_value is not None and not month_value.get('enabled', True):
                        continue
                    amount = Decimal(str(month_value.get('amount', head_config.base_amount)
                                         if month_value is not None else head_config.base_amount))
                    monthly_heads.setdefault(fee_head.id, {
                        'fee_head_id': fee_head.id,
                        'fee_head_name': fee_head.fee_head_name,
                        'amount': Decimal('0.00'),
                    })['amount'] += amount
            if not monthly_heads:
                raise HTTPException(404, f'No fee heads are assigned for {month_payment.fee_month}')

            ordered_heads = sorted(monthly_heads.values(), key=lambda head: head['fee_head_id'])
            total_amount = sum((head['amount'] for head in ordered_heads), Decimal('0.00'))
            if month_payment.payment_amount > total_amount:
                raise HTTPException(422, f'payment_amount cannot exceed the total fee for {month_payment.fee_month}')

            # Clear fee heads in ID order before applying payment to the next head.
            remaining_payment = month_payment.payment_amount
            paid_allocations = []
            for head in ordered_heads:
                head_paid = min(remaining_payment, head['amount'])
                paid_allocations.append(head_paid)
                remaining_payment -= head_paid

            detail_rows = [StudentFeeHead(
                fee_head_id=head['fee_head_id'], amount=head['amount'],
                discount_amount=Decimal('0.00'), fine_amount=Decimal('0.00'),
                net_amount=head['amount'], paid_amount=head_paid,
            ) for head, head_paid in zip(ordered_heads, paid_allocations)]
            item = StudentFee(
                school_id=school_id,
                academic_session_id=session_id,
                student_id=payload.student_id,
                fee_month=month_payment.fee_month,
                total_amount=total_amount,
                discount_amount=Decimal('0.00'),
                fine_amount=Decimal('0.00'),
                net_amount=total_amount,
                paid_amount=month_payment.payment_amount,
                status=calculate_student_fee_status(total_amount, month_payment.payment_amount),
                payment_mode=payload.payment_mode,
                description=payload.description,
                collected_by=payload.collected_by,
                fee_heads=detail_rows,
            )
            db.add(item)
            items.append(item)
        db.commit()
        for item in items:
            db.refresh(item)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'A fee record already exists for one of the student months') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Student fee records created successfully', 'total': len(items), 'data': items}


@router.get('/student_fees', response_model=StudentFeeListResult)
def get_student_fees(school_id: int, session_id: int, fee_id: int | None = None,
                     student_id: int | None = None, fee_month: str | None = None,
                     status: str | None = None, db: Session = Depends(fee_structure_school)):
    query = db.query(StudentFee).filter_by(school_id=school_id, academic_session_id=session_id)
    if fee_id is not None:
        query = query.filter_by(id=fee_id)
    if student_id is not None:
        query = query.filter_by(student_id=student_id)
    if fee_month is not None:
        if len(fee_month) != 7 or fee_month[4] != '-' or not fee_month[:4].isdigit() or not fee_month[5:].isdigit() or not 1 <= int(fee_month[5:]) <= 12:
            raise HTTPException(422, 'fee_month must use YYYY-MM format')
        query = query.filter_by(fee_month=fee_month)
    if status is not None:
        normalized_status = status.upper()
        if normalized_status not in {'UNPAID', 'PARTIAL', 'PAID'}:
            raise HTTPException(422, 'status must be UNPAID, PARTIAL, or PAID')
        query = query.filter_by(status=normalized_status)
    items = query.order_by(StudentFee.fee_month, StudentFee.student_id, StudentFee.id).all()
    return {'message': 'Student fee records fetched successfully', 'total': len(items), 'data': items}


def carry_forward_head(db, scope, first_month):
    carry_forward = db.query(StudentCarryForward).filter_by(**scope, month=first_month).one_or_none()
    zero = Decimal("0.00")
    return FeePaymentHeadStatus(
        fee_head_id=None,
        fee_head_name="Carry Forward",
        tag=CARRY_FORWARD_TAG,
        source_id=carry_forward.id if carry_forward is not None else None,
        payable_amount=carry_forward.amount if carry_forward is not None else zero,
        paid_amount=carry_forward.paid_amount if carry_forward is not None else zero,
        due_amount=carry_forward.unpaid_amount if carry_forward is not None else zero,
    )


def fee_adjustments_by_month(db, scope, months):
    heads = defaultdict(list)
    adjustments = (
        db.query(
            StudentFeeAdjustment.id,
            StudentFeeAdjustment.type,
            StudentFeeAdjustment.amount,
            StudentFeeAdjustment.date,
            StudentFeeAdjustment.frequency,
            StudentFeeAdjustment.discount_type,
        )
        .filter_by(**scope)
        .order_by(StudentFeeAdjustment.date, StudentFeeAdjustment.id)
        .all()
    )
    for adjustment in adjustments:
        month = adjustment.date.strftime("%Y-%m")
        if month not in months:
            continue
        amount = -adjustment.amount if adjustment.type == FeeAdjustmentType.DISCOUNT else adjustment.amount
        heads[month].append(FeePaymentAdjustmentStatus(
            fee_head_id=None,
            fee_head_name=adjustment.type,
            tag=adjustment.type.lower(),
            source_id=adjustment.id,
            frequency=adjustment.frequency,
            date=adjustment.date,
            discount_type=adjustment.discount_type,
            payable_amount=amount,
            paid_amount=Decimal("0.00"),
            due_amount=amount,
        ))
    return heads


@router.get('/fee_payments', response_model=FeePaymentStatusListResult)
def get_fee_payment_status(school_id: int, session_id: int, student_id: int,
                           fee_month: str | None = None,
                           db: Session = Depends(fee_structure_school)):
    student = db.query(Student.id, Student.class_id).filter_by(
        id=student_id, school_id=school_id, session_id=session_id,
    ).first()
    if student is None:
        raise HTTPException(404, 'Student not found for this school session')
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).first()
    months = list(session_months(session.start_date, session.end_date))
    if fee_month is not None:
        if fee_month not in months:
            raise HTTPException(422, 'fee_month is not part of this academic session')
        months = [fee_month]

    scope = dict(school_id=school_id, session_id=session_id, student_id=student_id)
    first_month = session.start_date.strftime("%Y-%m")
    carry_forward = carry_forward_head(db, scope, first_month) if first_month in months else None
    adjustment_heads = fee_adjustments_by_month(db, scope, set(months))
    assignments = db.query(FeeAssignment, FeeConfigurationHead).join(
        FeeConfigurationHead, FeeConfigurationHead.id == FeeAssignment.fee_configure_id,
    ).filter(
        FeeAssignment.school_id == school_id,
        FeeAssignment.session_id == session_id,
        FeeAssignment.class_id == student.class_id,
    ).all()
    configuration_ids = list({anchor.configuration_id for _, anchor in assignments})
    configured_rows = []
    if configuration_ids:
        configured_rows = db.query(FeeConfigurationHead, FeeHead).join(
            FeeHead, FeeHead.id == FeeConfigurationHead.fee_head_id,
        ).filter(FeeConfigurationHead.configuration_id.in_(configuration_ids)).all()

    payment_records = db.query(StudentFee).options(joinedload(StudentFee.fee_heads)).filter_by(
        school_id=school_id, academic_session_id=session_id, student_id=student_id,
    ).all()
    records_by_month = {record.fee_month: record for record in payment_records}
    data = []
    for month in months:
        month_heads = {}
        for head_config, fee_head in configured_rows:
            month_value = next((value for value in (head_config.monthly_config or [])
                                if value.get('month_key') == month), None)
            if month_value is not None and not month_value.get('enabled', True):
                continue
            amount = Decimal(str(month_value.get('amount', head_config.base_amount)
                                 if month_value is not None else head_config.base_amount))
            month_heads.setdefault(fee_head.id, {
                'fee_head_id': fee_head.id,
                'fee_head_name': fee_head.fee_head_name,
                'payable_amount': Decimal('0.00'),
            })['payable_amount'] += amount

        record = records_by_month.get(month)
        paid_by_head = {line.fee_head_id: line.paid_amount for line in record.fee_heads} if record else {}
        if record:
            # Keep the stored charge snapshot if configuration changed after payment.
            names = {head_id: values['fee_head_name'] for head_id, values in month_heads.items()}
            for line in record.fee_heads:
                month_heads[line.fee_head_id] = {
                    'fee_head_id': line.fee_head_id,
                    'fee_head_name': names.get(line.fee_head_id, line.fee_head_name),
                    'payable_amount': line.net_amount,
                }

        fee_heads = []
        for values in sorted(month_heads.values(), key=lambda item: (item['fee_head_name'].casefold(), item['fee_head_id'])):
            paid = min(paid_by_head.get(values['fee_head_id'], Decimal('0.00')), values['payable_amount'])
            fee_heads.append(FeePaymentHeadStatus(
                fee_head_id=values['fee_head_id'],
                fee_head_name=values['fee_head_name'],
                payable_amount=values['payable_amount'],
                paid_amount=paid,
                due_amount=values['payable_amount'] - paid,
            ))
        monthly_adjustments = adjustment_heads[month]
        month_carry_forward = carry_forward if month == first_month else None
        total_heads = fee_heads + monthly_adjustments
        if month_carry_forward is not None:
            total_heads.append(month_carry_forward)
        payable_amount = max(Decimal('0.00'), sum((head.payable_amount for head in total_heads), Decimal('0.00')))
        paid_amount = sum((head.paid_amount for head in total_heads), Decimal('0.00'))
        due_amount = max(Decimal('0.00'), payable_amount - paid_amount)
        data.append(FeePaymentMonthStatus(
            fee_month=month,
            payable_amount=payable_amount,
            paid_amount=paid_amount,
            due_amount=due_amount,
            status=StudentFeeStatus.PAID if due_amount == 0 else (
                StudentFeeStatus.UNPAID if paid_amount == 0 else StudentFeeStatus.PARTIAL
            ),
            fee_heads=fee_heads,
            fee_adjustment=monthly_adjustments,
            carry_forward=month_carry_forward,
        ))
    return {
        'message': 'Fee payment status fetched successfully',
        'total': len(data),
        'data': data,
    }


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
    if len(configuration_ids) > 1:
        raise HTTPException(
            422,
            'Multiple fee configurations exist for this fee structure; provide fee_configure_id to select the category',
        )
    # A structure ID is safe to resolve only when it has one configured group.
    return configuration_ids[0]


def validate_assignment_sections(db, school_id, session_id, class_id, section_ids):
    section_ids = list(dict.fromkeys(section_ids or []))
    if not section_ids:
        return []
    valid_ids = {row[0] for row in db.query(Section.id).filter(
        Section.id.in_(section_ids), Section.school_id == school_id,
        Section.session_id == session_id,
    ).all()}
    missing_ids = [section_id for section_id in section_ids if section_id not in valid_ids]
    if missing_ids:
        raise HTTPException(404, f'Section(s) not found for this school session: {missing_ids}')

    mapped_ids = {row[0] for row in db.query(ClassSectionMapping.section_id).filter(
        ClassSectionMapping.section_id.in_(section_ids),
        ClassSectionMapping.school_id == school_id,
        ClassSectionMapping.session_id == session_id,
        ClassSectionMapping.class_id == class_id,
    ).all()}
    for section_id in section_ids:
        if section_id not in mapped_ids:
            db.add(ClassSectionMapping(
                school_id=school_id, session_id=session_id,
                class_id=class_id, section_id=section_id,
            ))
    return section_ids


def section_id_array(value):
    """Normalize legacy scalar JSON values before returning or filtering assignments."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    normalized = []
    for section_id in values:
        try:
            section_id = int(section_id)
        except (TypeError, ValueError):
            continue
        if section_id > 0 and section_id not in normalized:
            normalized.append(section_id)
    return normalized


def assignment_response(db, item):
    section_ids = section_id_array(item.section_id)
    if section_ids:
        section_names_by_id = dict(db.query(Section.id, Section.name).filter(
            Section.id.in_(section_ids), Section.school_id == item.school_id,
        ).all())
    else:
        section_names_by_id = dict(db.query(Section.id, Section.name).join(
            ClassSectionMapping, ClassSectionMapping.section_id == Section.id,
        ).filter(
            ClassSectionMapping.school_id == item.school_id,
            ClassSectionMapping.session_id == item.session_id,
            ClassSectionMapping.class_id == item.class_id,
        ).order_by(Section.id).all())
        section_ids = list(section_names_by_id)
    section_names = [section_names_by_id[section_id] for section_id in section_ids
                     if section_id in section_names_by_id]
    return {
        'id': item.id, 'school_id': item.school_id, 'session_id': item.session_id,
        'class_id': item.class_id, 'class_name': item.class_name,
        'section_id': section_ids, 'section_name': section_names,
        'fee_configure_id': item.fee_configure_id, 'fee_structure_id': item.fee_structure_id,
        'fee_structure_name': item.fee_structure_name,
        'fee_category_id': item.fee_category_id, 'fee_category_name': item.fee_category_name,
    }


def save_assignment(db, item, payload):
    if db.query(SchoolClass.id).filter_by(
            id=payload.class_id, school_id=item.school_id, session_id=item.session_id).first() is None:
        raise HTTPException(404, 'Class not found for this school session')
    fee_configure_id = resolve_fee_configuration_id(
        db, item.school_id, item.session_id, payload.fee_configure_id, payload.fee_structure_id,
    )
    try:
        section_ids = validate_assignment_sections(
            db, item.school_id, item.session_id, payload.class_id, payload.section_id,
        )
        sibling = db.query(FeeAssignment).filter_by(
            school_id=item.school_id, session_id=item.session_id,
            class_id=payload.class_id, fee_configure_id=fee_configure_id,
        ).first()
        target = sibling or item
        target.class_id = payload.class_id
        target.section_id = section_ids
        target.fee_configure_id = fee_configure_id
        db.add(target)
        if target is not item:
            db.delete(item)
        db.commit()
        db.refresh(target)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Class already has a fee structure assigned or conflicts with database constraints') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Fee structure assignment updated successfully', 'data': assignment_response(db, target)}


@router.post('/fee_assignments', response_model=FeeAssignmentListResult, status_code=201)
def create_assignment(school_id: int, session_id: int, payload: FeeAssignmentBatchWrite, db: Session = Depends(fee_structure_school)):
    if db.query(SchoolClass.id).filter_by(
            id=payload.class_id, school_id=school_id, session_id=session_id).first() is None:
        raise HTTPException(404, 'Class not found for this school session')
    fee_configure_id = resolve_fee_configuration_id(
        db, school_id, session_id, payload.fee_configure_id, payload.fee_structure_id,
    )
    items = []
    try:
        section_ids = validate_assignment_sections(
            db, school_id, session_id, payload.class_id, payload.section_id,
        )
        item = db.query(FeeAssignment).filter_by(
            school_id=school_id, session_id=session_id, class_id=payload.class_id,
            fee_configure_id=fee_configure_id,
        ).first()
        if item is None:
            item = FeeAssignment(
                school_id=school_id, session_id=session_id, class_id=payload.class_id,
                section_id=section_ids, fee_configure_id=fee_configure_id,
            )
        else:
            item.section_id = section_ids
        db.add(item)
        items.append(item)
        db.commit()
        for item in items:
            db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'Fee assignment conflicts with a database constraint') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Fee structure assignment saved successfully', 'total': len(items),
            'data': [assignment_response(db, item) for item in items]}


@router.get('/fee_assignments', response_model=FeeAssignmentListResult | FeeAssignmentResult)
def get_assignments(school_id: int, session_id: int, assignment_id: int | None = None,
                    class_id: int | None = None, section_id: int | None = None,
                    fee_structure_id: int | None = None, fee_configure_id: int | None = None,
                    search: str | None = None, db: Session = Depends(fee_structure_school)):
    if assignment_id is not None:
        item = find_assignment(db, school_id, session_id, assignment_id)
        return {'message': 'Fee assignment fetched successfully', 'data': assignment_response(db, item)}
    query = db.query(FeeAssignment).options(
        joinedload(FeeAssignment.school_class),
        joinedload(FeeAssignment.fee_configuration).joinedload(FeeConfigurationHead.fee_structure),
        joinedload(FeeAssignment.fee_configuration).joinedload(FeeConfigurationHead.fee_category),
    ).filter_by(school_id=school_id, session_id=session_id)
    if class_id is not None:
        query = query.filter_by(class_id=class_id)
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
    if section_id is not None:
        items = [item for item in items if not section_id_array(item.section_id) or
                 section_id in section_id_array(item.section_id)]
    return {'message': 'Fee assignments fetched successfully', 'total': len(items),
            'data': [assignment_response(db, item) for item in items]}


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
        SchoolClass,
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
        SchoolClass.name,
    ).all()

    for assignment, structure, anchor, head_month, category, head, school_class in rows:
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
        assignment_data = assignment_response(db, assignment)
        structure_group['assignments'][assignment.id] = {
            'assignment_id': assignment.id,
            'class_id': assignment.class_id,
            'class_name': school_class.name,
            'section_id': assignment_data['section_id'],
            'section_name': assignment_data['section_name'],
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
def update_assignment(school_id: int, session_id: int, assignment_id: int, payload: FeeAssignmentBatchWrite, db: Session = Depends(fee_structure_school)):
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

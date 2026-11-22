from decimal import Decimal
from calendar import month_name
from collections import defaultdict
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, joinedload

from models.class_section import ClassSectionMapping, SchoolClass, Section
from models.fee_category import FeeCategory
from models.fee_assignment import FeeAssignment
from models.fee_configuration import FeeConfigurationHead
from models.fee_head import FeeHead
from models.fee_structure import FeeStructure
from models.generation_settings import FeeGenerationSettings
from models.session import Session as SchoolSession
from models.student import Student
from models.student_carry_forward import CARRY_FORWARD_TAG, StudentCarryForward
from models.student_fee_adjustment import StudentFeeAdjustment
from models.student_fee import StudentFee, StudentFeeChequePayment, StudentFeeHead
from routers.fee_structures import fee_structure_school
from schemas.fee_assignment import (
    FeeAssignmentWrite, FeeAssignmentBatchWrite, FeeAssignmentResult, FeeAssignmentListResult,
    AssignedFeeStructureDetailsResult,
)
from schemas.student_fee import (
    FeePaymentAdjustmentStatus, FeePaymentFeeHeadStatus, FeePaymentHeadStatus, FeePaymentMonthStatus,
    FeePaymentStatusListResult, calculate_student_fee_status,
    ChequePaymentStatus, StudentFeeChequeListResult, StudentFeeChequeResult, StudentFeeChequeStatusWrite,
    StudentFeeListResult, StudentFeeMonthPayment, StudentFeePaymentResult, StudentFeeWrite,
)
from schemas.enums import FeeAdjustmentStatus, FeeAdjustmentType, FeeType, PaymentMode
from utils.fee_months import session_months

router = APIRouter(tags=['Configure Fees'])
logger = logging.getLogger(__name__)

@router.post('/student_fees', response_model=StudentFeePaymentResult | StudentFeeChequeResult, status_code=201)
def create_student_fee(school_id: int, session_id: int, payload: StudentFeeWrite,
                       db: Session = Depends(fee_structure_school)):
    is_cheque = payload.payment_mode == PaymentMode.CHEQUE
    try:
        items, fee_head_paid, carry_forward_paid, penalty_paid, allocations = apply_student_fee_payment(
            db, school_id, session_id, payload, validate_only=is_cheque,
        )
        if is_cheque:
            cheque = StudentFeeChequePayment(
                school_id=school_id, session_id=session_id, student_id=payload.student_id,
                cheque_number=payload.cheque_number, description=payload.description,
                collected_by=payload.collected_by,
                months=[entry.model_dump(mode="json") for entry in allocations],
                late_fee_payment_amount=payload.late_fee_payment_amount,
            )
            db.add(cheque)
            db.commit()
            db.refresh(cheque)
            return {'message': 'Cheque payment recorded as pending', 'data': cheque}
        db.flush()
        balances = fee_payment_months(
            db, school_id, session_id, payload.student_id, months=[entry.fee_month for entry in payload.months],
        )
        db.commit()
        for item in items:
            db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        diagnostic = getattr(exc.orig, 'diag', None)
        sqlstate = getattr(exc.orig, 'pgcode', None)
        constraint = getattr(diagnostic, 'constraint_name', None)
        column = getattr(diagnostic, 'column_name', None)
        logger.warning(
            'Student fee payment failed a database integrity check (sqlstate=%s, constraint=%s, column=%s)',
            sqlstate, constraint, column,
        )
        reason = constraint or column or sqlstate
        detail = 'The database rejected the student fee payment'
        if reason:
            detail = f'{detail} ({reason})'
        raise HTTPException(409, detail) from exc
    except Exception:
        db.rollback()
        raise
    return {
        'message': 'Student fee records created successfully', 'total': len(items), 'data': items,
        'balances': balances, 'fee_head_paid_amount': fee_head_paid,
        'carry_forward_paid_amount': carry_forward_paid, 'penalty_paid_amount': penalty_paid,
        'late_fee_paid_amount': payload.total_late_fee_payment,
    }


def apply_student_fee_payment(db, school_id, session_id, payload, *, validate_only=False, gateway_mode=None):
    payment_mode = gateway_mode or payload.payment_mode
    student = db.query(Student.id).filter_by(
        id=payload.student_id, school_id=school_id, session_id=session_id,
    ).with_for_update().first()
    if student is None:
        raise HTTPException(404, 'Student not found for this school session')

    requested_months = [entry.fee_month for entry in payload.months]
    carry_forward = db.query(StudentCarryForward).filter_by(
        school_id=school_id, session_id=session_id, student_id=payload.student_id,
    ).filter(StudentCarryForward.month.in_(requested_months)).with_for_update().one_or_none()
    balances = fee_payment_months(
        db, school_id, session_id, payload.student_id, months=requested_months, include_pending_discounts=True,
    )
    by_month = {balance.fee_month: balance for balance in balances}
    if payload.settle_in_full:
        validate_full_payment(payload, by_month)

    allocations = payload.months
    regular_payment = payload.regular_payment_amount
    if regular_payment is not None:
        allocations = allocate_selected_months(balances, regular_payment)

    late_paid_by_month = {}
    for entry in payload.months:
        late_payment = payload.late_fee_payment_for(entry)
        if late_payment > 0:
            late_paid_by_month[entry.fee_month] = late_payment

    if payload.payment_amount is not None and late_paid_by_month:
        principal_by_month = {entry.fee_month: entry.payment_amount for entry in allocations}
        allocations = [
            StudentFeeMonthPayment(
                fee_month=month, payment_amount=principal_by_month.get(month, 0),
                late_fee_payment_amount=late_paid_by_month.get(month, 0) if not payload.late_fee_payment_amount else 0,
            )
            for month in sorted(principal_by_month.keys() | late_paid_by_month.keys())
        ]

    fee_scope = dict(school_id=school_id, academic_session_id=session_id, student_id=payload.student_id)
    records = {}
    accepted_by_month = defaultdict(Decimal)
    fee_head_paid = carry_forward_paid = penalty_paid = Decimal('0.00')
    for entry in allocations:
        month = entry.fee_month
        balance = by_month[month]
        discounts_by_head = {head.fee_head_id: head.discount_amount for head in balance.fee_heads}
        has_head_discount = any(discounts_by_head.values())
        discount_credit = sum(
            (line.effective_discount_amount for line in balance.fee_adjustment
             if line.tag == "discount" and line.fee_head_id is None),
            Decimal("0.00"),
        )
        if entry.payment_amount == 0 and not (
            (has_head_discount or discount_credit > 0)
            and (balance.due_amount == 0 or month in late_paid_by_month)
        ):
            continue
        if entry.payment_amount > 0 and balance.due_amount == 0 and payment_mode not in (PaymentMode.CHEQUE, PaymentMode.RAZORPAY):
            continue
        if entry.payment_amount > balance.due_amount:
            raise HTTPException(422, f'Payment amount cannot exceed the remaining balance for {month}')

        if not validate_only:
            accepted_by_month[month] += entry.payment_amount
        remaining = entry.payment_amount
        if carry_forward is not None and carry_forward.month == month and carry_forward.unpaid_amount > 0:
            carry_forward_paid = min(remaining, carry_forward.unpaid_amount)
            remaining -= carry_forward_paid
            if carry_forward_paid > 0:
                if not validate_only:
                    carry_forward.paid_amount += carry_forward_paid
                    records[month] = get_or_create_monthly_fee(db, fee_scope, month)
                if remaining == 0 and not has_head_discount:
                    continue

        due_by_head = {
            head.fee_head_id: head.principal_due
            for head in balance.fee_heads
        }
        head_due = sum(due_by_head.values(), Decimal('0.00'))
        head_payment = min(remaining, max(Decimal('0.00'), head_due - discount_credit))
        penalty_payment = remaining - head_payment
        if penalty_payment > 0:
            penalty_paid += allocate_penalty_payment(db, balance, penalty_payment, validate_only=validate_only)
        if validate_only:
            continue
        pending_discounts = [
            line.source_id for line in balance.fee_adjustment
            if line.tag == "discount" and line.status == FeeAdjustmentStatus.PENDING
            and line.effective_discount_amount > 0
            and (line.fee_head_id is not None or head_payment > 0 or head_due == discount_credit)
        ]
        if pending_discounts:
            applied_at = datetime.now(timezone.utc)
            for adjustment in db.query(StudentFeeAdjustment).filter(StudentFeeAdjustment.id.in_(pending_discounts)):
                adjustment.applied_at = applied_at

        if head_payment > 0 or has_head_discount or penalty_payment > 0:
            if month not in records:
                records[month] = get_or_create_monthly_fee(db, fee_scope, month)
        if head_payment == 0 and not has_head_discount:
            continue

        existing = records[month]
        if existing.net_amount == 0 and not existing.fee_heads:
            if not balance.fee_heads:
                raise HTTPException(404, f'No fee heads are assigned for {month}')
            for head in balance.fee_heads:
                principal = head.payable_amount + head.discount_amount - head.penalty_amount
                existing.fee_heads.append(StudentFeeHead(
                    fee_head_id=head.fee_head_id, amount=principal, net_amount=principal,
                    discount_amount=Decimal('0.00'), adjustment_discount_amount=Decimal('0.00'),
                    paid_amount=Decimal('0.00'),
                ))
            existing.net_amount = sum(head.amount for head in existing.fee_heads)
        elif head_payment > 0 and existing.due_amount == 0:
            raise HTTPException(409, f'Fee for {month} is already fully paid')

        for head in existing.fee_heads:
            discount = discounts_by_head.get(head.fee_head_id, Decimal("0.00"))
            change = discount - head.adjustment_discount_amount
            head.adjustment_discount_amount = discount
            head.discount_amount += change
            head.net_amount -= change
            existing.discount_amount += change
            existing.net_amount -= change

        unallocated = head_payment
        for head in existing.fee_heads:
            if unallocated == 0:
                break
            head_paid = min(unallocated, due_by_head.get(head.fee_head_id, Decimal("0.00")))
            head.paid_amount += head_paid
            unallocated -= head_paid
        if unallocated:
            raise HTTPException(409, f'Fee balance changed for {month}. Refresh the balance and try again.')
        fee_head_paid += head_payment
        existing.status = calculate_student_fee_status(existing.net_amount, existing.fee_head_paid_amount + head_payment)
        if head_payment > 0:
            existing.payment_mode = payment_mode
            existing.description = payload.description
            existing.collected_by = payload.collected_by
    for month, paid in late_paid_by_month.items() if not validate_only else ():
        if month not in records:
            records[month] = get_or_create_monthly_fee(db, fee_scope, month)
        records[month].late_fee_amount += paid
        accepted_by_month[month] += paid

    for month, record in records.items():
        record.paid_amount += accepted_by_month[month]
        balance = by_month[month]
        carry_amount = balance.carry_forward.payable_amount if balance.carry_forward else Decimal("0.00")
        if carry_forward is not None and carry_forward.month == month:
            carry_amount = carry_forward.amount
        gross_fees = (
            sum(head.original_amount for head in balance.fee_heads)
            if balance.fee_heads else max(Decimal("0.00"), record.net_amount + record.discount_amount - record.fine_amount)
        )
        record.total_amount = gross_fees + carry_amount + balance.total_penalty_amount + record.late_fee_amount
    return list(records.values()), fee_head_paid, carry_forward_paid, penalty_paid, allocations


def get_or_create_monthly_fee(db, scope, month):
    identity = dict(scope, fee_month=month)
    query = db.query(StudentFee).filter_by(**identity).with_for_update()
    record = query.one_or_none()
    if record is not None:
        return record

    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else postgresql_insert
    statement = insert(StudentFee).values(
        **identity, total_amount=0, net_amount=0, paid_amount=0, late_fee_amount=0, status='PAID',
    )
    db.execute(statement.on_conflict_do_nothing(index_elements=list(identity)))
    return query.one()


def validate_full_payment(payload, balances):
    if payload.payment_amount is None:
        amounts = [
            (entry.fee_month, entry.payment_amount, balances[entry.fee_month].due_amount)
            for entry in payload.months
        ]
    else:
        due = sum((balance.due_amount for balance in balances.values()), Decimal("0.00"))
        if payload.payment_amount_includes_late_fee:
            due += payload.total_late_fee_payment
        amounts = [(None, payload.payment_amount, due)]
    for month, received, required in amounts:
        if received != required:
            detail = {
                "message": "Payment amount must match the full remaining balance after discounts",
                "payment_amount": str(received),
                "required_amount": str(required),
            }
            if month is not None:
                detail["fee_month"] = month
            raise HTTPException(422, detail)


def allocate_selected_months(balances, amount):
    allocations = []
    remaining = amount
    for balance in sorted(balances, key=lambda balance: balance.fee_month):
        payment = min(remaining, balance.due_amount)
        if payment > 0:
            if payment > Decimal('9999999999.99'):
                raise HTTPException(422, f'Payment amount exceeds the supported limit for {balance.fee_month}')
            allocations.append(StudentFeeMonthPayment(fee_month=balance.fee_month, payment_amount=payment))
            remaining -= payment
        if remaining == 0:
            break
    if remaining > 0:
        raise HTTPException(422, 'Payment amount cannot exceed the remaining balance for the selected months')
    return allocations


def allocate_penalty_payment(db, balance, amount, *, validate_only):
    penalty_ids = [line.source_id for line in balance.fee_adjustment if line.tag == 'penalty' and line.due_amount > 0]
    penalties = db.query(StudentFeeAdjustment).filter(StudentFeeAdjustment.id.in_(penalty_ids)).order_by(
        StudentFeeAdjustment.date, StudentFeeAdjustment.id,
    ).with_for_update().all()
    remaining = amount
    for penalty in penalties:
        paid = min(remaining, penalty.amount - penalty.paid_amount)
        if not validate_only and paid > 0:
            penalty.paid_amount += paid
            if penalty.applied_at is None:
                penalty.applied_at = datetime.now(timezone.utc)
        remaining -= paid
    if remaining:
        raise HTTPException(409, f'Penalty balance changed for {balance.fee_month}. Refresh the balance and try again.')
    return amount


@router.get('/student_fees/cheque_payments', response_model=StudentFeeChequeListResult)
def list_student_fee_cheques(school_id: int, session_id: int, student_id: int | None = None,
                             status: ChequePaymentStatus | None = None,
                             db: Session = Depends(fee_structure_school)):
    query = db.query(StudentFeeChequePayment).filter_by(school_id=school_id, session_id=session_id)
    if student_id is not None:
        query = query.filter_by(student_id=student_id)
    if status is not None:
        query = query.filter_by(status=status)
    items = query.order_by(StudentFeeChequePayment.id.desc()).all()
    return {'message': 'Cheque payments fetched successfully', 'total': len(items), 'data': items}


@router.patch('/student_fees/cheque_payments/{cheque_id}', response_model=StudentFeeChequeResult)
def resolve_student_fee_cheque(school_id: int, session_id: int, cheque_id: int,
                               payload: StudentFeeChequeStatusWrite,
                               db: Session = Depends(fee_structure_school)):
    cheque = db.query(StudentFeeChequePayment).filter_by(
        id=cheque_id, school_id=school_id, session_id=session_id,
    ).with_for_update().first()
    if cheque is None:
        raise HTTPException(404, 'Cheque payment not found')
    if cheque.status == payload.status:
        return {'message': 'Cheque payment already resolved', 'data': cheque}
    if cheque.status != 'PENDING':
        raise HTTPException(409, 'Only a pending cheque payment can change status')

    try:
        if payload.status == 'SUCCESS':
            payment = StudentFeeWrite(
                student_id=cheque.student_id, months=cheque.months, payment_mode=PaymentMode.CHEQUE,
                cheque_number=cheque.cheque_number, description=cheque.description, collected_by=cheque.collected_by,
                late_fee_payment_amount=cheque.late_fee_payment_amount,
            )
            apply_student_fee_payment(db, school_id, session_id, payment)
        cheque.status = payload.status
        cheque.resolved_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(cheque)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'The cheque payment conflicts with an existing record') from exc
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(409, 'The pending cheque requires explicit late-fee amounts for its fee months') from exc
    except Exception:
        db.rollback()
        raise
    return {'message': 'Cheque payment status updated successfully', 'data': cheque}


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


def fee_adjustments_by_month(db, scope, months, *, exclude_adjustment_id=None):
    heads = defaultdict(list)
    query = (
        db.query(
            StudentFeeAdjustment.id,
            StudentFeeAdjustment.type,
            StudentFeeAdjustment.amount,
            StudentFeeAdjustment.paid_amount,
            StudentFeeAdjustment.date,
            StudentFeeAdjustment.frequency,
            StudentFeeAdjustment.discount_type,
            StudentFeeAdjustment.fee_head_id,
            StudentFeeAdjustment.status.label("status"),
            FeeHead.fee_head_name,
        )
        .filter_by(**scope)
        .outerjoin(FeeHead, FeeHead.id == StudentFeeAdjustment.fee_head_id)
    )
    if exclude_adjustment_id is not None:
        query = query.filter(StudentFeeAdjustment.id != exclude_adjustment_id)
    for adjustment in query.order_by(StudentFeeAdjustment.date, StudentFeeAdjustment.id):
        month = adjustment.date.strftime("%Y-%m")
        if month not in months:
            continue
        is_discount = adjustment.type == FeeAdjustmentType.DISCOUNT
        heads[month].append(FeePaymentAdjustmentStatus(
            fee_head_id=adjustment.fee_head_id,
            fee_head_name=adjustment.fee_head_name or adjustment.type,
            status=adjustment.status,
            tag=adjustment.type.lower(),
            source_id=adjustment.id,
            frequency=adjustment.frequency,
            date=adjustment.date,
            discount_type=adjustment.discount_type,
            amount=adjustment.amount,
            discount_amount=adjustment.amount if is_discount else Decimal("0.00"),
            penalty_amount=Decimal("0.00") if is_discount else adjustment.amount,
            penalty_paid_amount=Decimal("0.00") if is_discount else adjustment.paid_amount,
            payable_amount=adjustment.amount,
            paid_amount=Decimal("0.00") if is_discount else adjustment.paid_amount,
            due_amount=Decimal("0.00") if is_discount else adjustment.amount - adjustment.paid_amount,
        ))
    return heads


@router.get('/fee_payments', response_model=FeePaymentStatusListResult)
def get_fee_payment_status(school_id: int, session_id: int, student_id: int,
                           fee_month: str | None = None,
                           include_pending_discounts: bool = False,
                           db: Session = Depends(fee_structure_school)):
    """Return posted balances. Set include_pending_discounts to preview a new payment."""
    data = fee_payment_months(
        db, school_id, session_id, student_id, months=[fee_month] if fee_month is not None else None,
        include_pending_discounts=include_pending_discounts,
    )
    scope = dict(school_id=school_id, session_id=session_id, student_id=student_id)
    settings = late_fee_settings(db, scope)
    return {
        'message': 'Fee payment status fetched successfully',
        'total': len(data),
        'data': data,
        'late_fee': {
            'enabled': settings.late_fee_enabled,
            'type': settings.late_fee_type,
            'late_fee_amount': settings.late_fee_amount,
            'min_due_percentage': settings.min_due_amount,
            'generation_day': settings.generation_day,
            'payment_due_day': settings.payment_due_day,
        } if settings is not None else {},
    }


def late_fee_settings(db, scope):
    return db.query(FeeGenerationSettings).filter_by(
        school_id=scope["school_id"], session_id=scope["session_id"], fee_type=FeeType.SCHOOL_FEE,
    ).one_or_none()


def fee_payment_months(
    db, school_id, session_id, student_id, *, months=None, exclude_adjustment_id=None,
    include_pending_discounts=False,
):
    student = db.query(Student.id, Student.class_id).filter_by(
        id=student_id, school_id=school_id, session_id=session_id,
    ).first()
    if student is None:
        raise HTTPException(404, 'Student not found for this school session')
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school_id).first()
    session_fee_months = list(session_months(session.start_date, session.end_date))
    if months is None:
        months = session_fee_months
    elif any(month not in session_fee_months for month in months):
        raise HTTPException(422, 'fee_month is not part of this academic session')

    scope = dict(school_id=school_id, session_id=session_id, student_id=student_id)
    first_month = session.start_date.strftime("%Y-%m")
    carry_forward = carry_forward_head(db, scope, first_month) if first_month in months else None
    adjustment_heads = fee_adjustments_by_month(db, scope, set(months), exclude_adjustment_id=exclude_adjustment_id)
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
    ).filter(StudentFee.fee_month.in_(months)).all()
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
                    'original_amount': line.amount,
                    'payable_amount': line.net_amount + line.adjustment_discount_amount,
                }

        monthly_adjustments = adjustment_heads[month]
        discounts_by_head = defaultdict(Decimal)
        penalties_by_head = defaultdict(Decimal)
        penalty_paid_by_head = defaultdict(Decimal)
        credit_order = sorted(monthly_adjustments, key=lambda line: line.status == FeeAdjustmentStatus.PENDING)
        if not include_pending_discounts:
            credit_order = [
                line for line in credit_order
                if line.tag != "discount" or line.status != FeeAdjustmentStatus.PENDING
            ]
        for adjustment in credit_order:
            if adjustment.fee_head_id is None:
                continue
            if adjustment.tag == "discount":
                head_id = adjustment.fee_head_id
                if head_id in month_heads:
                    principal = month_heads[head_id]['payable_amount']
                    paid = paid_by_head.get(head_id, Decimal("0.00"))
                    available = max(Decimal("0.00"), principal - paid - discounts_by_head[head_id])
                    adjustment.effective_discount_amount = min(adjustment.discount_amount, available)
                    discounts_by_head[head_id] += adjustment.effective_discount_amount
            else:
                penalties_by_head[adjustment.fee_head_id] += adjustment.payable_amount
                penalty_paid_by_head[adjustment.fee_head_id] += adjustment.paid_amount

        fee_heads = []
        for values in sorted(month_heads.values(), key=lambda item: (item['fee_head_name'].casefold(), item['fee_head_id'])):
            paid = min(paid_by_head.get(values['fee_head_id'], Decimal('0.00')), values['payable_amount'])
            head_id = values['fee_head_id']
            discount = discounts_by_head[head_id]
            penalty = penalties_by_head[head_id]
            penalty_paid = penalty_paid_by_head[head_id]
            payable = values['payable_amount'] - discount + penalty
            paid += penalty_paid
            fee_heads.append(FeePaymentFeeHeadStatus(
                fee_head_id=values['fee_head_id'],
                fee_head_name=values['fee_head_name'],
                original_amount=values.get('original_amount', values['payable_amount']),
                payable_amount=payable,
                paid_amount=paid,
                due_amount=payable - paid,
                discount_amount=discount,
                penalty_amount=penalty,
                penalty_paid_amount=penalty_paid,
            ))
        heads_by_id = {head.fee_head_id: head for head in fee_heads}
        for adjustment in monthly_adjustments:
            head = heads_by_id.get(adjustment.fee_head_id)
            if head is not None:
                adjustment.fee_head_payable_amount = head.payable_amount
                adjustment.fee_head_paid_amount = head.paid_amount
                adjustment.fee_head_due_amount = head.due_amount
                adjustment.fee_head_status = head.status

        remaining_principal = sum((head.principal_due for head in fee_heads), Decimal("0.00"))
        month_discount = Decimal("0.00")
        for adjustment in credit_order:
            if adjustment.tag == "discount" and adjustment.fee_head_id is None:
                credit = min(adjustment.discount_amount, remaining_principal)
                adjustment.effective_discount_amount = credit
                month_discount += credit
                remaining_principal -= credit
        month_carry_forward = carry_forward if month == first_month else None
        total_heads = fee_heads + [
            line for line in monthly_adjustments
            if line.tag == "penalty" and line.fee_head_id not in month_heads
        ]
        if month_carry_forward is not None:
            total_heads.append(month_carry_forward)
        late_fee_paid = record.late_fee_amount if record else Decimal("0.00")
        payable_amount = late_fee_paid + max(
            Decimal("0.00"), sum((head.payable_amount for head in total_heads), Decimal("0.00")) - month_discount,
        )
        paid_amount = late_fee_paid + sum((head.paid_amount for head in total_heads), Decimal("0.00"))
        due_amount = max(Decimal("0.00"), payable_amount - paid_amount)
        data.append(FeePaymentMonthStatus(
            fee_month=month,
            payable_amount=payable_amount,
            paid_amount=paid_amount,
            late_fee_paid_amount=late_fee_paid,
            due_amount=due_amount,
            status=calculate_student_fee_status(payable_amount, paid_amount),
            fee_heads=fee_heads,
            fee_adjustment=monthly_adjustments,
            carry_forward=month_carry_forward,
        ))
    return data


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

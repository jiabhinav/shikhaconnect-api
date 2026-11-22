from enum import Enum


class StudentFeeStatus(str, Enum):
    UNPAID = "UNPAID"
    PARTIAL = "PARTIAL"
    PAID = "PAID"


class PaymentMode(str, Enum):
    CASH = "Cash"
    CHEQUE = "Cheque"
    CREDIT_CARD = "Credit Card"
    DEBIT_CARD = "Debit Card"
    UPI = "UPI"
    NET_BANKING = "Net Banking"
    DEMAND_DRAFT = "Demand-Draft"
    BANK_DEPOSIT = "Bank Deposit"
    OTHERS = "Others"
    RAZORPAY = "Razorpay"


class FeeAdjustmentType(str, Enum):
    DISCOUNT = "Discount"
    PENALTY = "Penalty"


class FeeAdjustmentStatus(str, Enum):
    PENDING = "Pending"
    PARTIAL = "Partial"
    PAID = "Paid"


class FeeAdjustmentFrequency(str, Enum):
    ONE_TIME = "One Time"
    MONTHLY = "Monthly"


class FeeDiscountType(str, Enum):
    CHARITY = "Charity"
    SIBLING = "Sibling"
    OTHERS = "Others"


class LateFeeType(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"


class FeeType(str, Enum):
    SCHOOL_FEE = "school_fee"
    TRANSPORT = "transport"

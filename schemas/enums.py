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

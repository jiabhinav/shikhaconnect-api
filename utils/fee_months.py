"""Resolve calendar-month fee columns from a session and its overrides."""
from decimal import Decimal

MONTH_COLUMNS = {
    4: 'april_fee', 5: 'may_fee', 6: 'june_fee', 7: 'july_fee',
    8: 'august_fee', 9: 'september_fee', 10: 'october_fee',
    11: 'november_fee', 12: 'december_fee', 1: 'january_fee',
    2: 'february_fee', 3: 'march_fee',
}


def monthly_fees(amount, overrides, start_date, end_date):
    values = {}
    for month, column in MONTH_COLUMNS.items():
        year = start_date.year + (month < start_date.month)
        key = f'{year:04}-{month:02}'
        if key > end_date.strftime('%Y-%m'):
            values[column] = Decimal('0')
            continue
        value = overrides.get(key, amount)
        if isinstance(value, dict):
            value = value['amount'] if value['enabled'] else '0'
        values[column] = Decimal(str(value))
    return values


def session_months(start_date, end_date):
    """Yield year-qualified months in academic-session order."""
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        yield f'{year:04}-{month:02}'
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def month_schedule(amount, overrides, start_date, end_date):
    rows = []
    for month in session_months(start_date, end_date):
        value = overrides.get(month, amount)
        enabled = value['enabled'] if isinstance(value, dict) else True
        entered = Decimal(str(value['amount'] if isinstance(value, dict) else value))
        rows.append({'month': month, 'amount': entered if enabled else None, 'enabled': enabled,
                     'effective_amount': entered if enabled else Decimal('0.00'),
                     'is_override': month in overrides})
    return rows

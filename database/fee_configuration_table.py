"""Add and backfill the twelve fee columns on existing installations."""
from sqlalchemy import inspect, select, text

from utils.fee_months import MONTH_COLUMNS, monthly_fees


def ensure_fee_month_columns(connection):
    table_name = 'fee_configuration_heads'
    if not inspect(connection).has_table(table_name):
        return
    columns = {col['name'] for col in inspect(connection).get_columns(table_name)}
    if set(MONTH_COLUMNS.values()).issubset(columns):
        return
    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(731904218)'))
        columns = {col['name'] for col in inspect(connection).get_columns(table_name)}
    missing = [name for name in MONTH_COLUMNS.values() if name not in columns]
    if not missing:
        return
    for name in missing:
        connection.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {name} NUMERIC(12, 2) NOT NULL DEFAULT 0'))
    from models.fee_configuration import FeeConfiguration, FeeConfigurationHead
    from models.session import Session as SchoolSession
    head, config, session = FeeConfigurationHead.__table__, FeeConfiguration.__table__, SchoolSession.__table__
    rows = connection.execute(select(head.c.id, head.c.amount, head.c.month_amounts, session.c.start_date, session.c.end_date).select_from(
        head.join(config, head.c.configuration_id == config.c.id).join(session, config.c.session_id == session.c.id)
    )).mappings().all()
    for row in rows:
        values = monthly_fees(row['amount'], row['month_amounts'] or {}, row['start_date'], row['end_date'])
        connection.execute(head.update().where(head.c.id == row['id']).values(**{name: values[name] for name in missing}))

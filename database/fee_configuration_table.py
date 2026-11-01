"""Migrate fee configuration heads from wide month columns to month rows."""
from sqlalchemy import MetaData, Table, inspect, select, text

from utils.fee_months import session_months


def migrate_fee_configuration_month_rows(connection):
    table_name = 'fee_configuration_heads'
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return
    columns = {column['name'] for column in inspector.get_columns(table_name)}
    if {'month', 'base_amount', 'enabled', 'is_override', 'has_checkbox'}.issubset(columns):
        return

    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(731904218)'))
        # Another worker may have completed the conversion while this one
        # waited on the transaction lock. Re-read the schema before converting.
        columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
        if {'month', 'base_amount', 'enabled', 'is_override', 'has_checkbox'}.issubset(columns):
            return

    from models.fee_configuration import FeeConfigurationHead
    from models.session import Session as SchoolSession
    from models.fee_configuration import FeeConfiguration

    old = Table(table_name, MetaData(), autoload_with=connection)
    config = FeeConfiguration.__table__
    session = SchoolSession.__table__
    legacy_rows = connection.execute(select(
        old.c.id, old.c.configuration_id, old.c.fee_head_id, old.c.amount,
        old.c.month_amounts, session.c.start_date, session.c.end_date,
    ).select_from(old.join(config, old.c.configuration_id == config.c.id).join(
        session, config.c.session_id == session.c.id
    )).order_by(old.c.id)).mappings().all()

    converted = []
    for old_row in legacy_rows:
        overrides = old_row['month_amounts'] or {}
        months = session_months(old_row['start_date'], old_row['end_date'])
        for index, month in enumerate(months):
            value = overrides.get(month, old_row['amount'])
            enabled = value.get('enabled', True) if isinstance(value, dict) else True
            month_amount = value['amount'] if isinstance(value, dict) else value
            converted.append({
                **({'id': old_row['id']} if index == 0 else {}),
                'configuration_id': old_row['configuration_id'],
                'fee_head_id': old_row['fee_head_id'],
                'base_amount': old_row['amount'],
                'month': month,
                'amount': month_amount,
                'enabled': enabled,
                'is_override': month in overrides,
                'has_checkbox': isinstance(value, dict),
            })

    # There are no tables referencing fee_configuration_heads IDs. Keep the
    # first row ID for each old head; new months receive fresh IDs.
    old.drop(connection)
    FeeConfigurationHead.__table__.create(connection)
    for row in converted:
        connection.execute(FeeConfigurationHead.__table__.insert().values(**row))

    if connection.dialect.name == 'postgresql':
        connection.execute(text(
            "SELECT setval(pg_get_serial_sequence('fee_configuration_heads', 'id'), "
            "COALESCE((SELECT MAX(id) FROM fee_configuration_heads), 1), "
            "EXISTS (SELECT 1 FROM fee_configuration_heads))"
        ))

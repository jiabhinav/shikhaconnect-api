"""Migrate fee configuration storage to one JSONB row per configured head."""
from database.advisory_lock import acquire_transaction_lock
from sqlalchemy import MetaData, Table, inspect, select, text

from utils.fee_months import MONTH_COLUMNS, session_months


TARGET_COLUMNS = {
    'configuration_id', 'school_id', 'session_id', 'fee_structure_id', 'fee_category_id',
    'fee_head_id', 'base_amount',
    'edit_months_wise', 'monthly_config', 'created_at', 'updated_at',
}


def migrate_fee_configuration_month_rows(connection):
    table_name = 'fee_configuration_heads'
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return

    if connection.dialect.name == 'postgresql':
        acquire_transaction_lock(connection, 731904218)

    from models.fee_configuration import FeeConfigurationHead

    has_parent_table = inspect(connection).has_table('fee_configurations')
    if has_parent_table:
        config_columns = {column['name'] for column in inspect(connection).get_columns('fee_configurations')}
        if 'fee_head_settings' not in config_columns:
            connection.execute(text(
                "ALTER TABLE fee_configurations ADD COLUMN fee_head_settings JSON NOT NULL DEFAULT '{}'"
            ))

    columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
    if TARGET_COLUMNS.issubset(columns):
        current = Table(table_name, MetaData(), autoload_with=connection)
        for row in connection.execute(select(current.c.id, current.c.monthly_config)).mappings():
            monthly_config = row['monthly_config'] or []
            normalized = []
            changed = False
            for month in monthly_config:
                value = dict(month)
                if isinstance(value.get('amount'), str):
                    value['amount'] = float(value['amount'])
                    changed = True
                normalized.append(value)
            if changed:
                connection.execute(current.update().where(current.c.id == row['id']).values(
                    monthly_config=normalized))
        if has_parent_table:
            connection.execute(text('DROP TABLE fee_configurations'))
        return

    old = Table(table_name, MetaData(), autoload_with=connection)
    if not has_parent_table:
        raise RuntimeError('Cannot migrate fee configuration heads without the legacy fee_configurations table')
    config = Table('fee_configurations', MetaData(), autoload_with=connection)
    config_rows = connection.execute(select(config)).mappings().all()
    configurations = {row['id']: row for row in config_rows}
    source_rows = connection.execute(select(old).order_by(old.c.id)).mappings().all()

    converted = []
    if 'month' in columns:
        grouped = {}
        for row in source_rows:
            key = (row['configuration_id'], row['fee_head_id'])
            group = grouped.setdefault(key, {'rows': [], 'ids': []})
            group['rows'].append(row)
            group['ids'].append(row['id'])

        for (configuration_id, fee_head_id), group in grouped.items():
            config_row = configurations[configuration_id]
            head_state = (config_row.get('fee_head_settings') or {}).get(str(fee_head_id), {})
            sorted_rows = sorted(group['rows'], key=lambda row: row['month'])
            monthly_config = []
            for row in sorted_rows:
                month_state = head_state.get('months', {}).get(row['month'], {})
                enabled = row.get('enabled', month_state.get('enabled', True))
                is_override = row.get('is_override', month_state.get('is_override', False))
                monthly_config.append({
                    'month_key': row['month'],
                    'amount': float(row['amount']),
                    'enabled': bool(enabled),
                    'is_override': bool(is_override),
                })
            first_row = sorted_rows[0]
            converted.append({
                'configuration_id': configuration_id,
                'school_id': config_row['school_id'],
                'session_id': config_row['session_id'],
                'fee_structure_id': config_row['fee_structure_id'],
                'fee_category_id': config_row['fee_category_id'],
                'fee_head_id': fee_head_id,
                'base_amount': head_state.get('base_amount', str(first_row.get('base_amount', first_row['amount']))),
                'edit_months_wise': bool(head_state.get(
                    'edit_months_wise', any(item['is_override'] for item in monthly_config))),
                'monthly_config': monthly_config,
            })
    else:
        # Older installations held one row/head with wide month columns and/or
        # a month_amounts JSON object. Expand that information into JSON entries.
        sessions = Table('sessions', MetaData(), autoload_with=connection)
        session_rows = {row['id']: row for row in connection.execute(select(sessions)).mappings()}
        for row in source_rows:
            config_row = configurations[row['configuration_id']]
            session = session_rows[config_row['session_id']]
            months = list(session_months(session['start_date'], session['end_date']))
            overrides = row.get('month_amounts') or {}
            month_values = []
            for month in months:
                value = overrides.get(month)
                if isinstance(value, dict):
                    amount = value.get('amount', row.get('amount', 0))
                    enabled = value.get('enabled', True)
                    is_override = value.get('is_override', True)
                elif value is not None:
                    amount, enabled, is_override = value, True, True
                else:
                    legacy_column = MONTH_COLUMNS.get(int(month[5:]))
                    amount = row.get(legacy_column, row.get('amount', 0))
                    enabled, is_override = True, False
                month_values.append({
                    'month_key': month, 'amount': float(amount),
                    'enabled': bool(enabled), 'is_override': bool(is_override),
                })
            config_settings = (config_row.get('fee_head_settings') or {}).get(str(row['fee_head_id']), {})
            converted.append({
                'configuration_id': row['configuration_id'],
                'school_id': config_row['school_id'],
                'session_id': config_row['session_id'],
                'fee_structure_id': config_row['fee_structure_id'],
                'fee_category_id': config_row['fee_category_id'],
                'fee_head_id': row['fee_head_id'],
                'base_amount': str(config_settings.get('base_amount', row.get('amount', 0))),
                'edit_months_wise': bool(config_settings.get('edit_months_wise', bool(overrides))),
                'monthly_config': month_values,
            })

    old.drop(connection)
    FeeConfigurationHead.__table__.create(connection)
    next_id = max(configurations, default=0) + 1
    next_id = max(next_id, 1)
    ids_reserved = set(configurations)
    for row in converted:
        group_id = row['configuration_id']
        if group_id in ids_reserved:
            row_id = group_id
            ids_reserved.remove(group_id)
        else:
            while next_id in ids_reserved:
                next_id += 1
            row_id = next_id
            next_id += 1
        connection.execute(FeeConfigurationHead.__table__.insert().values(
            id=row_id, **row,
        ))
    if connection.dialect.name == 'postgresql' and converted:
        connection.execute(text(
            "SELECT setval(pg_get_serial_sequence('fee_configuration_heads', 'id'), "
            "(SELECT COALESCE(MAX(id), 1) FROM fee_configuration_heads))"
        ))
    connection.execute(text('DROP TABLE fee_configurations'))

"""Migrate fee configuration heads to one minimal row per session month."""
from sqlalchemy import MetaData, Table, inspect, select, text

from utils.fee_months import MONTH_COLUMNS, session_months

MONTH_ROW_COLUMNS = {'configuration_id', 'fee_head_id', 'month', 'amount'}
STATE_COLUMNS = {'base_amount', 'enabled', 'is_override', 'has_checkbox'}
LEGACY_MONTH_COLUMNS = set(MONTH_COLUMNS.values()) | {'month_amounts'}


def migrate_fee_configuration_month_rows(connection):
    table_name = 'fee_configuration_heads'
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return

    from models.fee_configuration import FeeConfiguration, FeeConfigurationHead
    from models.session import Session as SchoolSession

    config = FeeConfiguration.__table__
    config_columns = {column['name'] for column in inspect(connection).get_columns('fee_configurations')}
    if 'fee_head_settings' not in config_columns:
        default = "'{}'" if connection.dialect.name == 'sqlite' else "'{}'"
        connection.execute(text(
            f'ALTER TABLE fee_configurations ADD COLUMN fee_head_settings JSON NOT NULL DEFAULT {default}'
        ))
        config = Table('fee_configurations', MetaData(), autoload_with=connection)

    columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
    if MONTH_ROW_COLUMNS.issubset(columns) and not (STATE_COLUMNS | LEGACY_MONTH_COLUMNS).intersection(columns):
        return

    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(731904218)'))
        # Recheck after waiting in case a different worker completed the migration.
        columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
        config_columns = {column['name'] for column in inspect(connection).get_columns('fee_configurations')}
        if MONTH_ROW_COLUMNS.issubset(columns) and not (STATE_COLUMNS | LEGACY_MONTH_COLUMNS).intersection(columns) and 'fee_head_settings' in config_columns:
            return

    old = Table(table_name, MetaData(), autoload_with=connection)
    settings_by_config = {
        row['id']: dict(row['fee_head_settings'] or {})
        for row in connection.execute(select(config.c.id, config.c.fee_head_settings)).mappings()
    }
    converted = []

    if 'month' in columns:
        source_rows = connection.execute(select(old).order_by(old.c.id)).mappings().all()
        grouped_state = {}
        for row in source_rows:
            configuration_id, fee_head_id = row['configuration_id'], row['fee_head_id']
            key = (configuration_id, fee_head_id)
            head_state = grouped_state.setdefault(key, {
                'base_amount': row.get('base_amount', row['amount']),
                'edit_months_wise': False,
                'months': {},
            })
            enabled = row.get('enabled', True)
            is_override = row.get('is_override', False)
            has_checkbox = row.get('has_checkbox', False)
            head_state['edit_months_wise'] = head_state['edit_months_wise'] or is_override
            head_state['months'][row['month']] = {
                'enabled': enabled, 'is_override': is_override, 'has_checkbox': has_checkbox,
            }
            converted.append({
                'id': row['id'], 'configuration_id': configuration_id,
                'fee_head_id': fee_head_id, 'month': row['month'], 'amount': row['amount'],
            })
        for (configuration_id, fee_head_id), state in grouped_state.items():
            settings_by_config.setdefault(configuration_id, {})[str(fee_head_id)] = {
                'base_amount': str(state['base_amount']),
                'edit_months_wise': state['edit_months_wise'],
                'months': state['months'],
            }
    else:
        session = SchoolSession.__table__
        legacy_rows = connection.execute(select(
            old, session.c.start_date, session.c.end_date,
        ).select_from(old.join(config, old.c.configuration_id == config.c.id).join(
            session, config.c.session_id == session.c.id
        )).order_by(old.c.id)).mappings().all()
        for row in legacy_rows:
            overrides = row.get('month_amounts') or {}
            month_settings = {}
            months = list(session_months(row['start_date'], row['end_date']))
            for index, month in enumerate(months):
                value = overrides.get(month, row['amount'])
                enabled = value.get('enabled', True) if isinstance(value, dict) else True
                monthly_amount = value['amount'] if isinstance(value, dict) else value
                month_settings[month] = {
                    'enabled': enabled, 'is_override': month in overrides,
                    'has_checkbox': isinstance(value, dict),
                }
                converted.append({
                    'id': row['id'] if index == 0 else None,
                    'source_id': row['id'],
                    'legacy_values': {column.name: row[column.name] for column in old.columns},
                    'configuration_id': row['configuration_id'],
                    'fee_head_id': row['fee_head_id'],
                    'month': month, 'amount': monthly_amount,
                })
            settings_by_config.setdefault(row['configuration_id'], {})[str(row['fee_head_id'])] = {
                'base_amount': str(row['amount']),
                'edit_months_wise': bool(overrides), 'months': month_settings,
            }

    # Save base amounts and checkbox/override state outside the minimal month table.
    for configuration_id, settings in settings_by_config.items():
        connection.execute(config.update().where(config.c.id == configuration_id).values(fee_head_settings=settings))

    if connection.dialect.name == 'postgresql':
        _migrate_postgresql_rows(connection, old, columns, converted)
        return

    # SQLite needs a table rebuild to remove the old per-head unique constraint.
    old.drop(connection)
    FeeConfigurationHead.__table__.create(connection)
    for row in converted:
        insert_values = {key: value for key, value in row.items() if key in MONTH_ROW_COLUMNS}
        if row.get('id') is not None:
            insert_values['id'] = row['id']
        connection.execute(FeeConfigurationHead.__table__.insert().values(**insert_values))


def _migrate_postgresql_rows(connection, old, columns, converted):
    table_name = 'fee_configuration_heads'
    quote = connection.dialect.identifier_preparer.quote

    # The original key allowed one row per fee head; drop it before adding months.
    for constraint in inspect(connection).get_unique_constraints(table_name):
        if constraint.get('name') == 'uq_fee_configuration_head':
            connection.execute(text(
                f'ALTER TABLE {table_name} DROP CONSTRAINT {quote(constraint["name"])}'
            ))

    if 'month' not in columns:
        connection.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {quote("month")} VARCHAR(7)'))
        target = Table(table_name, MetaData(), autoload_with=connection)
        for row in converted:
            source_id = row['source_id']
            if row['id'] is not None:
                connection.execute(target.update().where(target.c.id == source_id).values(
                    month=row['month'], amount=row['amount']))
            else:
                # Copy legacy NOT NULL columns, then override the monthly amount.
                legacy_values = {key: value for key, value in row['legacy_values'].items()
                                 if key != 'id'}
                legacy_values.update(configuration_id=row['configuration_id'],
                                     fee_head_id=row['fee_head_id'], month=row['month'], amount=row['amount'])
                connection.execute(target.insert().values(**legacy_values))

    # Intermediate month-row schema had these state columns; move them to the
    # configuration JSON above and keep only the requested table fields.
    constraint_names = {item.get('name') for item in inspect(connection).get_check_constraints(table_name)}
    if 'ck_fee_configuration_base_amount' in constraint_names:
        connection.execute(text(
            f'ALTER TABLE {table_name} DROP CONSTRAINT {quote("ck_fee_configuration_base_amount")}'
        ))
    current_columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
    for name in STATE_COLUMNS | LEGACY_MONTH_COLUMNS:
        if name in current_columns:
            connection.execute(text(f'ALTER TABLE {table_name} DROP COLUMN {quote(name)}'))

    current_columns = {column['name'] for column in inspect(connection).get_columns(table_name)}
    if 'month' in current_columns:
        connection.execute(text(f'ALTER TABLE {table_name} ALTER COLUMN {quote("month")} SET NOT NULL'))
    unique_names = {item.get('name') for item in inspect(connection).get_unique_constraints(table_name)}
    if 'uq_fee_configuration_head_month' not in unique_names:
        connection.execute(text(
            f'ALTER TABLE {table_name} ADD CONSTRAINT uq_fee_configuration_head_month '
            'UNIQUE (configuration_id, fee_head_id, month)'
        ))

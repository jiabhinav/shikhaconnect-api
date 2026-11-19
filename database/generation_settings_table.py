from sqlalchemy import inspect

from database.advisory_lock import acquire_transaction_lock
from models.generation_settings import FeeGenerationSettings


def merge_generation_settings(connection):
    table = FeeGenerationSettings.__table__
    legacy_transport = "transport_generation_settings"
    inspector = inspect(connection)
    has_fee = inspector.has_table(table.name)
    has_transport = inspector.has_table(legacy_transport)
    if not has_transport and (not has_fee or _has_single_due_column(inspector, table.name)):
        return

    is_postgresql = connection.dialect.name == "postgresql"
    if is_postgresql:
        acquire_transaction_lock(connection, 731904218)
        inspector = inspect(connection)
        has_fee = inspector.has_table(table.name)
        has_transport = inspector.has_table(legacy_transport)

    columns = {column["name"] for column in inspector.get_columns(table.name)} if has_fee else set()
    alter_fee = is_postgresql and {"payment_due_day", "payment_due_days"} <= columns
    rebuild_fee = has_fee and "payment_due_days" in columns and not alter_fee
    legacy_fee = "fee_generation_settings_before_merge"
    with connection.begin_nested():
        if alter_fee:
            _consolidate_postgresql_due_columns(connection)

        if rebuild_fee:
            connection.exec_driver_sql(f"ALTER TABLE {table.name} RENAME TO {legacy_fee}")

        if not has_fee or rebuild_fee:
            table.create(connection)

        if rebuild_fee:
            _copy_settings(connection, legacy_fee, preserve_ids=True)
            if is_postgresql:
                connection.exec_driver_sql(
                    "SELECT setval(pg_get_serial_sequence('fee_generation_settings', 'id'), "
                    "COALESCE(MAX(id), 1), COUNT(*) > 0) FROM fee_generation_settings"
                )

        if has_transport:
            if is_postgresql:
                connection.exec_driver_sql(f"LOCK TABLE {legacy_transport} IN ACCESS EXCLUSIVE MODE")
            _copy_settings(connection, legacy_transport, legacy_transport=True)
            connection.exec_driver_sql(f"DROP TABLE {legacy_transport}")

        if rebuild_fee:
            connection.exec_driver_sql(f"DROP TABLE {legacy_fee}")


def _consolidate_postgresql_due_columns(connection):
    connection.exec_driver_sql(
        "ALTER TABLE fee_generation_settings DROP CONSTRAINT IF EXISTS ck_generation_settings_payment_due"
    )
    connection.exec_driver_sql(
        "UPDATE fee_generation_settings SET payment_due_day = payment_due_days WHERE fee_type = 'school_fee'"
    )
    connection.exec_driver_sql(
        "ALTER TABLE fee_generation_settings ALTER COLUMN payment_due_day SET NOT NULL, "
        "DROP COLUMN payment_due_days, ADD CONSTRAINT ck_generation_settings_payment_due CHECK ("
        "(fee_type = 'school_fee' AND payment_due_day >= 0) OR "
        "(fee_type = 'transport' AND payment_due_day BETWEEN 1 AND 31))"
    )


def _has_single_due_column(inspector, table_name):
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    return "payment_due_day" in columns and "payment_due_days" not in columns


def _copy_settings(connection, source, *, preserve_ids=False, legacy_transport=False):
    source_columns = {column["name"] for column in inspect(connection).get_columns(source)}
    shared_columns = (
        "session_id", "generation_day", "late_fee_enabled",
        "late_fee_type", "late_fee_amount", "min_due_amount", "created_at", "updated_at",
    )
    columns = [name for name in shared_columns if name in source_columns]
    if preserve_ids:
        columns.insert(0, "id")
    target_columns = ", ".join([*columns, "school_id", "fee_type", "payment_due_day"])
    source_values = ", ".join(f"legacy.{name}" for name in columns)
    if legacy_transport:
        fee_type, due_day = "'transport'", "legacy.payment_due_day"
    elif "payment_due_day" in source_columns:
        fee_type = "legacy.fee_type"
        due_day = "CASE WHEN legacy.fee_type = 'school_fee' THEN legacy.payment_due_days ELSE legacy.payment_due_day END"
    else:
        fee_type, due_day = "'school_fee'", "legacy.payment_due_days"

    conflict_clause = " ON CONFLICT (session_id, fee_type) DO NOTHING" if legacy_transport else ""
    connection.exec_driver_sql(
        f"INSERT INTO fee_generation_settings ({target_columns}) "
        f"SELECT {source_values}, "
        "(SELECT school_id FROM sessions WHERE id = legacy.session_id), "
        f"{fee_type}, {due_day} FROM {source} AS legacy ORDER BY legacy.id{conflict_clause}"
    )

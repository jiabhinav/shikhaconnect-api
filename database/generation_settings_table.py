from sqlalchemy import inspect

from database.advisory_lock import acquire_transaction_lock
from models.generation_settings import FeeGenerationSettings


def merge_generation_settings(connection):
    table = FeeGenerationSettings.__table__
    legacy_transport = "transport_generation_settings"
    inspector = inspect(connection)
    has_fee = inspector.has_table(table.name)
    has_transport = inspector.has_table(legacy_transport)
    if not has_transport and (not has_fee or _is_shared_table(inspector, table.name)):
        return

    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904218)
        inspector = inspect(connection)
        has_fee = inspector.has_table(table.name)
        has_transport = inspector.has_table(legacy_transport)

    rebuild_fee = has_fee and not _is_shared_table(inspector, table.name)
    legacy_fee = "fee_generation_settings_before_merge"
    with connection.begin_nested():
        if rebuild_fee:
            connection.exec_driver_sql(f"ALTER TABLE {table.name} RENAME TO {legacy_fee}")

        if not has_fee or rebuild_fee:
            table.create(connection)

        if rebuild_fee:
            _copy_settings(connection, legacy_fee, "school_fee", "payment_due_days", preserve_ids=True)
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql(
                    "SELECT setval(pg_get_serial_sequence('fee_generation_settings', 'id'), "
                    "COALESCE(MAX(id), 1), COUNT(*) > 0) FROM fee_generation_settings"
                )

        if has_transport:
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql(f"LOCK TABLE {legacy_transport} IN ACCESS EXCLUSIVE MODE")
            _copy_settings(connection, legacy_transport, "transport", "payment_due_day")
            connection.exec_driver_sql(f"DROP TABLE {legacy_transport}")

        if rebuild_fee:
            connection.exec_driver_sql(f"DROP TABLE {legacy_fee}")


def _is_shared_table(inspector, table_name):
    return "payment_due_day" in {column["name"] for column in inspector.get_columns(table_name)}


def _copy_settings(connection, source, fee_type, due_column, *, preserve_ids=False):
    source_columns = {column["name"] for column in inspect(connection).get_columns(source)}
    shared_columns = (
        "session_id", "generation_day", due_column, "late_fee_enabled",
        "late_fee_type", "late_fee_amount", "min_due_amount", "created_at", "updated_at",
    )
    columns = [name for name in shared_columns if name in source_columns]
    if preserve_ids:
        columns.insert(0, "id")
    target_columns = ", ".join([*columns, "school_id", "fee_type"])
    source_values = ", ".join(f"legacy.{name}" for name in columns)
    conflict_clause = " ON CONFLICT (session_id, fee_type) DO NOTHING" if fee_type == "transport" else ""
    connection.exec_driver_sql(
        f"INSERT INTO fee_generation_settings ({target_columns}) "
        f"SELECT {source_values}, "
        "(SELECT school_id FROM sessions WHERE id = legacy.session_id), "
        f"'{fee_type}' FROM {source} AS legacy ORDER BY legacy.id{conflict_clause}"
    )

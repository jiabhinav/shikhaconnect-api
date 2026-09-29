"""Preserve existing staff permissions when renaming their module reference."""
from sqlalchemy import inspect, text


def add_staff_permission_actions(connection):
    """Add action flags with read access enabled by default."""
    if not inspect(connection).has_table("staff_permission"):
        return
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    columns = {column["name"] for column in inspect(connection).get_columns("staff_permission")}
    for name in ("read", "delete", "update", "create"):
        if name not in columns:
            default = "true" if name == "read" else "false"
            connection.execute(text(
                f'ALTER TABLE staff_permission ADD COLUMN "{name}" BOOLEAN NOT NULL DEFAULT {default}'
            ))
    if connection.dialect.name == "postgresql":
        connection.execute(text('ALTER TABLE staff_permission ALTER COLUMN "read" SET DEFAULT true'))


def rename_staff_module_id(connection):
    inspector = inspect(connection)
    if not inspector.has_table("staff_permission"):
        return
    columns = {column["name"] for column in inspector.get_columns("staff_permission")}
    if "module_id" not in columns or "staff_module_id" in columns:
        return
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
        columns = {column["name"] for column in inspect(connection).get_columns("staff_permission")}
        if "staff_module_id" in columns:
            return
    connection.execute(text(
        "ALTER TABLE staff_permission RENAME COLUMN module_id TO staff_module_id"
    ))

"""Preserve existing staff permissions when renaming their module reference."""
from sqlalchemy import inspect, text


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

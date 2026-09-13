"""Compatibility repair for permissions created before module IDs were used."""
from sqlalchemy import inspect, text


def allow_legacy_service_name_null(connection):
    if connection.dialect.name != "postgresql":
        return
    inspector = inspect(connection)
    if not inspector.has_table("school_permissions"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("school_permissions")}
    legacy = columns.get("service_name")
    if "module_id" in columns and legacy is not None and not legacy["nullable"]:
        # Keep historical names; new permissions are identified by module_id.
        connection.execute(text("ALTER TABLE school_permissions ALTER COLUMN service_name DROP NOT NULL"))

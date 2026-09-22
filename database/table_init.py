"""Register application models and create missing tables in dependency order."""
from importlib import import_module
from pkgutil import walk_packages
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from database.database import Base
from database.user_address_table import migrate_user_addresses
from database.staff_account_table import migrate_user_accounts, migrate_staff_profiles, migrate_account_status
from database.school_permissions_table import allow_legacy_service_name_null
from database.session_table import remove_legacy_session_year_index
from database.staff_permission_table import rename_staff_module_id
from database.school_mapping_table import migrate_school_mapping_accounts
from database.session_catalogs import migrate_session_catalogs


def register_models():
    import models

    for module in walk_packages(models.__path__, prefix="models."):
        import_module(module.name)



def migrate_legacy_users(connection):
    """Split legacy accounts inside the caller's initialization transaction."""
    inspector = inspect(connection)
    if not inspector.has_table("users"):
        return
    if "login_user_id" in {col["name"] for col in inspector.get_columns("users")}:
        return
    if connection.dialect.name != "postgresql":
        raise SQLAlchemyError("Automatic legacy user migration requires PostgreSQL")

    # Serialize startup workers, then inspect again in case another migrated.
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    connection.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
    inspector = inspect(connection)
    if "login_user_id" in {col["name"] for col in inspector.get_columns("users")}:
        return
    if inspector.has_table("login_user"):
        raise SQLAlchemyError(
            "Legacy users and login_user both exist; reconcile the partial migration "
            "before restarting. No existing account data was overwritten."
        )

    migration = Path(__file__).resolve().parent.parent / "migrations" / "005_split_login_user.sql"
    # The SQL file can also run standalone; startup owns its transaction here.
    sql = "\n".join(
        line for line in migration.read_text().splitlines()
        if line.strip() not in {"BEGIN;", "COMMIT;"}
    )
    connection.exec_driver_sql(sql)


def remove_user_school_name(connection):
    """Schools are linked through school_mapping, not a name on users."""
    inspector = inspect(connection)
    if not inspector.has_table("users"):
        return
    if "school_name" not in {column["name"] for column in inspector.get_columns("users")}:
        return
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
        if "school_name" not in {column["name"] for column in inspect(connection).get_columns("users")}:
            return
    connection.execute(text("ALTER TABLE users DROP COLUMN school_name"))


def ensure_all_tables(connection):
    register_models()
    migrate_user_accounts(connection)
    remove_user_school_name(connection)
    rename_staff_module_id(connection)
    allow_legacy_service_name_null(connection)
    remove_legacy_session_year_index(connection)
    existing = set(inspect(connection).get_table_names())
    if set(Base.metadata.tables).issubset(existing):
        migrate_staff_profiles(connection)
        migrate_user_addresses(connection)
        migrate_account_status(connection)
        migrate_school_mapping_accounts(connection)
        migrate_session_catalogs(connection)
        return
    if connection.dialect.name == "postgresql":
        # Recheck within create_all after serializing concurrent initialization.
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    Base.metadata.create_all(bind=connection, checkfirst=True)
    migrate_staff_profiles(connection)
    migrate_user_addresses(connection)
    migrate_account_status(connection)
    migrate_school_mapping_accounts(connection)
    migrate_session_catalogs(connection)

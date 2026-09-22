"""Consolidate legacy addresses and permissions under login accounts."""
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from models.staff import StaffAddress

ADDRESS_FIELDS = ("line_1", "line_2", "city", "country", "state", "pin_code")


def migrate_account_owner(connection, table):
    """Translate legacy staff/account IDs to the login_user primary key."""
    columns = {col["name"] for col in inspect(connection).get_columns(table)}
    owners = columns.intersection({"staff_id", "user_id"})
    if not owners:
        return
    connection.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
    if "login_user_id" not in columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN login_user_id INTEGER"))
    for owner in sorted(owners):
        source = "staff u" if owner == "staff_id" else "users u"
        source_id = "u.id"
        joined_source = source
        conflict = connection.execute(text(f"""
            SELECT 1 FROM {table} a LEFT JOIN {joined_source} ON {source_id}=a.{owner}
            WHERE a.{owner} IS NOT NULL AND (u.login_user_id IS NULL OR
                (a.login_user_id IS NOT NULL AND a.login_user_id <> u.login_user_id)) LIMIT 1
        """)).first()
        if conflict:
            raise SQLAlchemyError(f"Cannot migrate {table}: missing or conflicting account owner")
        connection.execute(text(f"""
            UPDATE {table} a SET login_user_id=u.login_user_id FROM {source} WHERE {source_id}=a.{owner}
        """))
    connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN login_user_id SET NOT NULL"))
    for constraint in inspect(connection).get_check_constraints(table):
        if constraint["name"] == "ck_staff_address_owner":
            connection.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT ck_staff_address_owner"))
    for owner in sorted(owners):
        connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {owner}"))
    if not any(fk["constrained_columns"] == ["login_user_id"] and fk["referred_table"] == "login_user"
               for fk in inspect(connection).get_foreign_keys(table)):
        connection.execute(text(f"""
            ALTER TABLE {table} ADD FOREIGN KEY (login_user_id) REFERENCES login_user(id) ON DELETE CASCADE
        """))
    from database.staff_account_table import ensure_unique
    ensure_unique(connection, table, ["login_user_id", "staff_module_id"] if table == "staff_permission" else ["login_user_id"])
    connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_login_user_id ON {table} (login_user_id)"))


def migrate_user_addresses(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    legacy_fields = [field for field in ADDRESS_FIELDS if field in user_columns]
    legacy_owners = any(
        {"staff_id", "user_id"}.intersection(col["name"] for col in inspector.get_columns(table))
        for table in ("staff_address", "staff_permission") if table in tables
    )
    if not legacy_fields and "staff_addrers" not in tables and not legacy_owners:
        return
    if connection.dialect.name != "postgresql":
        raise SQLAlchemyError("Legacy address migration requires PostgreSQL")
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    StaffAddress.__table__.create(connection, checkfirst=True)
    for table in ("staff_address", "staff_permission"):
        if inspect(connection).has_table(table):
            migrate_account_owner(connection, table)
    for field in ADDRESS_FIELDS:
        connection.execute(text(f"ALTER TABLE staff_address ALTER COLUMN {field} DROP NOT NULL"))
    connection.execute(text("ALTER TABLE staff_address ALTER COLUMN country TYPE VARCHAR(255)"))
    if "staff_addrers" in tables:
        connection.execute(text("LOCK TABLE staff_addrers IN ACCESS EXCLUSIVE MODE"))
        names = ", ".join(ADDRESS_FIELDS)
        selected = ", ".join(f"a.{field}" for field in ADDRESS_FIELDS)
        connection.execute(text(f"""
            INSERT INTO staff_address (login_user_id, {names})
            SELECT (SELECT u.login_user_id FROM users u WHERE u.id=a.user_id), {selected}
            FROM staff_addrers a
        """))
        connection.execute(text("DROP TABLE staff_addrers"))
    connection.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
    columns = {col["name"] for col in inspect(connection).get_columns("users")}
    fields = [field for field in ADDRESS_FIELDS if field in columns]
    if fields:
        names = ", ".join(fields)
        populated = " OR ".join(f"{field} IS NOT NULL" for field in fields)
        connection.execute(text(f"""
            INSERT INTO staff_address (login_user_id, {names})
            SELECT login_user_id, {names} FROM users WHERE {populated}
        """))
        for field in fields:
            connection.execute(text(f"ALTER TABLE users DROP COLUMN {field}"))

"""Move school assignments from user profiles to shared login accounts."""
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError


def migrate_school_mapping_accounts(connection):
    inspector = inspect(connection)
    if not inspector.has_table("school_mapping"):
        return
    foreign_keys = inspector.get_foreign_keys("school_mapping")
    owners = [fk for fk in foreign_keys if fk["constrained_columns"] == ["user_id"]]
    if len(owners) == 1 and owners[0]["referred_table"] == "login_user":
        return
    if connection.dialect.name != "postgresql":
        raise SQLAlchemyError("Legacy school mapping migration requires PostgreSQL")
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    connection.execute(text("LOCK TABLE school_mapping IN ACCESS EXCLUSIVE MODE"))
    owners = [fk for fk in inspect(connection).get_foreign_keys("school_mapping")
              if fk["constrained_columns"] == ["user_id"]]
    if len(owners) == 1 and owners[0]["referred_table"] == "login_user":
        return
    if len(owners) != 1 or owners[0]["referred_table"] != "users":
        raise SQLAlchemyError("Cannot determine legacy school mapping account owner")
    connection.execute(text("LOCK TABLE users, login_user IN SHARE MODE"))
    if connection.execute(text("""
        SELECT 1 FROM school_mapping m
        LEFT JOIN users u ON u.id=m.user_id
        LEFT JOIN login_user l ON l.id=u.login_user_id
        WHERE l.id IS NULL LIMIT 1
    """)).first():
        raise SQLAlchemyError("Cannot migrate school mapping: missing login account")

    quote = connection.dialect.identifier_preparer.quote
    # Swapped profile/account IDs can temporarily collide within one school.
    # Restore the original uniqueness constraints after translating all rows.
    constraints = connection.execute(text("""
        SELECT conname, pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE conrelid='school_mapping'::regclass AND contype='u'
    """)).all()
    for name, _ in constraints:
        connection.execute(text(f"ALTER TABLE school_mapping DROP CONSTRAINT {quote(name)}"))
    connection.execute(text(
        f"ALTER TABLE school_mapping DROP CONSTRAINT {quote(owners[0]['name'])}"
    ))
    connection.execute(text("""
        UPDATE school_mapping m SET user_id=u.login_user_id FROM users u WHERE u.id=m.user_id
    """))
    for name, definition in constraints:
        connection.execute(text(f"ALTER TABLE school_mapping ADD CONSTRAINT {quote(name)} {definition}"))
    connection.execute(text("""
        ALTER TABLE school_mapping ADD CONSTRAINT school_mapping_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES login_user(id) ON DELETE CASCADE
    """))

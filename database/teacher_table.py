"""Allow the optional city field on previously created teacher addresses."""
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError


def migrate_teacher_employee_codes(connection):
    if not inspect(connection).has_table("teachers"):
        return
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    columns = {c["name"] for c in inspect(connection).get_columns("teachers")}
    for name, definition in (("employee_code", "VARCHAR(150)"), ("employee_sequence", "INTEGER")):
        if name not in columns:
            connection.execute(text(f"ALTER TABLE teachers ADD COLUMN {name} {definition}"))
    names = {c["name"] for c in inspect(connection).get_unique_constraints("teachers")}
    names.update(index["name"] for index in inspect(connection).get_indexes("teachers"))
    for name, field in (("uq_teacher_employee_code", "employee_code"),
                        ("uq_teacher_employee_sequence", "employee_sequence")):
        if name not in names:
            connection.execute(text(f"CREATE UNIQUE INDEX {name} ON teachers (school_id, {field})"))


def migrate_optional_teacher_city(connection):
    if not inspect(connection).has_table("teacher_addresses"):
        return
    columns = {c["name"]: c for c in inspect(connection).get_columns("teacher_addresses")}
    if columns["city"]["nullable"]:
        return
    if connection.dialect.name != "postgresql":
        raise SQLAlchemyError("Existing teacher address city migration requires PostgreSQL")
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    connection.execute(text("ALTER TABLE teacher_addresses ALTER COLUMN city DROP NOT NULL"))


def migrate_optional_teacher_identity(connection):
    for table, field in (("teachers", "aadhaar_number"),):
        if not inspect(connection).has_table(table):
            continue
        columns = {c["name"]: c for c in inspect(connection).get_columns(table)}
        if columns[field]["nullable"]:
            continue
        if connection.dialect.name != "postgresql":
            raise SQLAlchemyError("Existing teacher identity migration requires PostgreSQL")
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
        connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN {field} DROP NOT NULL"))


def migrate_teacher_logins(connection):
    """Copy credentials without changing password hashes or other account profiles."""
    if not inspect(connection).has_table("teachers"):
        return
    columns = {c["name"] for c in inspect(connection).get_columns("teachers")}
    if "login_user_id" not in columns:
        return
    if connection.dialect.name != "postgresql":
        raise SQLAlchemyError("Existing teacher login migration requires PostgreSQL")
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    connection.execute(text("LOCK TABLE teachers, teacher_login, login_user IN ACCESS EXCLUSIVE MODE"))
    columns = {c["name"] for c in inspect(connection).get_columns("teachers")}
    if "login_user_id" not in columns:
        return
    if "teacher_login_id" in columns:
        raise SQLAlchemyError("Partial teacher login migration detected; reconcile before retrying")
    rows = connection.execute(text("""
        SELECT t.id AS teacher_id, t.school_id, t.login_user_id, l.first_name,
               l.middle_name, l.last_name, l.mobile, l.email, l.password, l.status
        FROM teachers t LEFT JOIN login_user l ON l.id = t.login_user_id
    """)).mappings().all()
    if any(row["mobile"] is None or row["password"] is None for row in rows):
        raise SQLAlchemyError("A teacher login account is missing; migration stopped")
    connection.execute(text("ALTER TABLE teachers ADD COLUMN teacher_login_id INTEGER REFERENCES teacher_login(id) UNIQUE"))
    for row in rows:
        login_id = connection.execute(text("""
            INSERT INTO teacher_login (school_id, first_name, middle_name, last_name,
                                       mobile, email, password, role, status)
            VALUES (:school_id, :first_name, :middle_name, :last_name,
                    :mobile, :email, :password, 'Teacher', :status) RETURNING id
        """), dict(row)).scalar_one()
        connection.execute(text("UPDATE teachers SET teacher_login_id=:login_id WHERE id=:teacher_id"),
                           {"login_id": login_id, "teacher_id": row["teacher_id"]})
    connection.execute(text("ALTER TABLE teachers ALTER COLUMN teacher_login_id SET NOT NULL"))
    connection.execute(text("ALTER TABLE teachers DROP COLUMN login_user_id"))
    for row in rows:
        # Preserve accounts shared with admins/staff or holding existing grants/addresses.
        connection.execute(text("""
            DELETE FROM login_user l WHERE l.id=:old_id
              AND NOT EXISTS (SELECT 1 FROM users u WHERE u.login_user_id=l.id)
              AND NOT EXISTS (SELECT 1 FROM staff s WHERE s.login_user_id=l.id)
              AND NOT EXISTS (SELECT 1 FROM school_mapping m WHERE m.user_id=l.id)
              AND NOT EXISTS (SELECT 1 FROM staff_permission p WHERE p.login_user_id=l.id)
              AND NOT EXISTS (SELECT 1 FROM staff_address a WHERE a.login_user_id=l.id)
        """), {"old_id": row["login_user_id"]})

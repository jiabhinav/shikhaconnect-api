"""Add student section storage without inventing sections for existing students."""
from sqlalchemy import inspect, text


def migrate_student_section(connection):
    if not inspect(connection).has_table("students"):
        return
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    if "section_id" in {column["name"] for column in inspect(connection).get_columns("students")}:
        return
    connection.execute(text(
        "ALTER TABLE students ADD COLUMN section_id INTEGER REFERENCES sections(id) ON DELETE RESTRICT"
    ))
    connection.execute(text("CREATE INDEX ix_students_section_id ON students (section_id)"))


def migrate_optional_father_fields(connection):
    """Allow omitted father contact/Aadhaar values on existing PostgreSQL tables."""
    if not inspect(connection).has_table("students"):
        return
    fields = ("father_contact_no", "father_aadhaar_no")
    columns = {column["name"]: column for column in inspect(connection).get_columns("students")}
    if not any(field in columns and not columns[field]["nullable"] for field in fields):
        return
    if connection.dialect.name != "postgresql":
        from sqlalchemy.exc import SQLAlchemyError
        raise SQLAlchemyError("Existing student father-field migration requires PostgreSQL")
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    for field in fields:
        if field in columns and not columns[field]["nullable"]:
            connection.execute(text(f"ALTER TABLE students ALTER COLUMN {field} DROP NOT NULL"))


def migrate_optional_student_pin_codes(connection):
    """Allow absent PIN codes in both legacy and separate student addresses."""
    for table in ("students", "student_addresses"):
        inspector = inspect(connection)
        if not inspector.has_table(table):
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        if "pin_code" not in columns or columns["pin_code"]["nullable"]:
            continue
        if connection.dialect.name != "postgresql":
            from sqlalchemy.exc import SQLAlchemyError
            raise SQLAlchemyError("Existing student PIN-code migration requires PostgreSQL")
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
        connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN pin_code DROP NOT NULL"))


def migrate_student_roll_number(connection):
    """Add optional roll numbers, preserving existing student records."""
    if not inspect(connection).has_table("students"):
        return
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    if "roll_number" in {column["name"] for column in inspect(connection).get_columns("students")}:
        return
    connection.execute(text("ALTER TABLE students ADD COLUMN roll_number VARCHAR(50)"))


def migrate_student_admission_storage(connection):
    """Move login-owned student details and reverse the link in one transaction."""
    from sqlalchemy.exc import SQLAlchemyError

    inspector = inspect(connection)
    if not inspector.has_table("students"):
        return
    columns = {c["name"] for c in inspector.get_columns("students")}
    login_columns = ({c["name"] for c in inspector.get_columns("student_login")}
                     if inspector.has_table("student_login") else set())
    if {"login_id", "admission_number", "admission_sequence", "status"} <= columns and "student_id" not in login_columns:
        return
    if connection.dialect.name != "postgresql":
        raise SQLAlchemyError("Existing student admission storage migration requires PostgreSQL")
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    connection.execute(text("LOCK TABLE students, student_login IN ACCESS EXCLUSIVE MODE"))
    columns = {c["name"] for c in inspect(connection).get_columns("students")}
    login_columns = {c["name"] for c in inspect(connection).get_columns("student_login")}
    for name, definition in (
        ("login_id", "INTEGER REFERENCES student_login(id) ON DELETE RESTRICT UNIQUE"),
        ("admission_number", "VARCHAR(150)"),
        ("admission_sequence", "INTEGER"),
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
    ):
        if name not in columns:
            connection.execute(text(f"ALTER TABLE students ADD COLUMN {name} {definition}"))
    if "student_id" in login_columns:
        conflict = connection.execute(text("""
            SELECT 1 FROM student_login l LEFT JOIN students s ON s.id = l.student_id
            WHERE s.id IS NULL OR s.school_id <> l.school_id
               OR (s.login_id IS NOT NULL AND s.login_id <> l.id)
               OR (s.admission_number IS NOT NULL AND s.admission_number <> l.admission_number)
               OR (s.admission_sequence IS NOT NULL AND s.admission_sequence <> l.admission_sequence)
            LIMIT 1
        """)).first()
        if conflict:
            raise SQLAlchemyError("Conflicting student/login data; admission migration stopped without discarding data")
        connection.execute(text("""
            UPDATE students s SET login_id = l.id, admission_number = l.admission_number,
                admission_sequence = l.admission_sequence, status = l.status
            FROM student_login l WHERE l.student_id = s.id
        """))
        for name in ("student_id", "admission_number", "admission_sequence", "status"):
            connection.execute(text(f"ALTER TABLE student_login DROP COLUMN {name}"))
    constraints = {c["name"] for c in inspect(connection).get_unique_constraints("students")}
    for name, field in (("uq_student_admission", "admission_number"),
                        ("uq_student_admission_sequence", "admission_sequence")):
        if name not in constraints:
            connection.execute(text(f"ALTER TABLE students ADD CONSTRAINT {name} UNIQUE (school_id, {field})"))
    checks = {c["name"] for c in inspect(connection).get_check_constraints("students")}
    if "ck_student_status" not in checks:
        connection.execute(text("ALTER TABLE students ADD CONSTRAINT ck_student_status CHECK (status IN ('active', 'inactive'))"))


def migrate_shared_student_logins(connection):
    """Preserve child APAR IDs before consolidating same-school mobile logins."""
    inspector = inspect(connection)
    if not inspector.has_table("students") or not inspector.has_table("student_login"):
        return
    student_columns = {c["name"] for c in inspector.get_columns("students")}
    login_columns = {c["name"] for c in inspector.get_columns("student_login")}
    unique = inspector.get_unique_constraints("students")
    login_unique = inspector.get_unique_constraints("student_login")
    if ("apar_id" in student_columns and "apar_id" not in login_columns
            and not any(c["column_names"] == ["login_id"] for c in unique)
            and any(c["column_names"] == ["school_id", "mobile"] for c in login_unique)):
        return
    if connection.dialect.name != "postgresql":
        from sqlalchemy.exc import SQLAlchemyError
        raise SQLAlchemyError("Existing shared student login migration requires PostgreSQL")
    connection.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
    connection.execute(text("LOCK TABLE students, student_login IN ACCESS EXCLUSIVE MODE"))
    inspector = inspect(connection)
    if "apar_id" not in {c["name"] for c in inspector.get_columns("students")}:
        connection.execute(text("ALTER TABLE students ADD COLUMN apar_id VARCHAR(100)"))
    if "apar_id" in {c["name"] for c in inspector.get_columns("student_login")}:
        connection.execute(text("UPDATE students s SET apar_id=l.apar_id FROM student_login l WHERE s.login_id=l.id AND s.apar_id IS NULL"))
        connection.execute(text("ALTER TABLE student_login DROP COLUMN apar_id"))
    for constraint in inspect(connection).get_unique_constraints("students"):
        if constraint["column_names"] == ["login_id"]:
            name = connection.dialect.identifier_preparer.quote(constraint["name"])
            connection.execute(text(f"ALTER TABLE students DROP CONSTRAINT {name}"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_students_login_id ON students (login_id)"))
    connection.execute(text("""
        UPDATE students s SET login_id = canonical.id
        FROM student_login old,
             (SELECT school_id, mobile, MIN(id) AS id FROM student_login GROUP BY school_id, mobile) canonical
        WHERE s.login_id=old.id AND old.school_id=canonical.school_id AND old.mobile=canonical.mobile
    """))
    connection.execute(text("""
        DELETE FROM student_login old USING student_login canonical
        WHERE old.school_id=canonical.school_id AND old.mobile=canonical.mobile AND old.id>canonical.id
    """))
    if not any(c["column_names"] == ["school_id", "mobile"] for c in inspect(connection).get_unique_constraints("student_login")):
        connection.execute(text("ALTER TABLE student_login ADD CONSTRAINT uq_student_login_school_mobile UNIQUE (school_id, mobile)"))

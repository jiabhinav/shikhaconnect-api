from sqlalchemy import text

from models.subject import Subject


def ensure_subject_table(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904221)"))
    Subject.__table__.create(bind=connection, checkfirst=True)

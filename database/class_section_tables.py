from sqlalchemy import text

from models.class_section import SchoolClass, Section


def ensure_class_section_tables(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904220)"))
    for model in (SchoolClass, Section):
        model.__table__.create(bind=connection, checkfirst=True)

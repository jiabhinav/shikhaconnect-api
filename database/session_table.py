from sqlalchemy import text

from models.session import Session as SchoolSession


def ensure_session_table(connection):
    """Create the session table if absent, serializing PostgreSQL workers."""
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904219)"))
    SchoolSession.__table__.create(bind=connection, checkfirst=True)

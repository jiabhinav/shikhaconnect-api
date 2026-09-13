from sqlalchemy import text

from models.session import Session as SchoolSession


def ensure_session_table(connection):
    """Create the session table if absent, serializing PostgreSQL workers."""
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904219)"))
    SchoolSession.__table__.create(bind=connection, checkfirst=True)


def update_school_session(db, school, school_info):
    """Synchronize the school's configured session, preserving other sessions."""
    ensure_session_table(db.connection())
    session = db.query(SchoolSession).filter_by(
        school_id=school.id,
        name=school.session_name,
        start_date=school.session_start_date,
        end_date=school.session_end_date,
    ).order_by(SchoolSession.id).first()
    if session is None:
        session = SchoolSession(school_id=school.id)
        db.add(session)
    session.name = school_info.session_name
    session.start_date = school_info.session_start_date
    session.end_date = school_info.session_end_date

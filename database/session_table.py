from sqlalchemy import text

from models.session import Session as SchoolSession


def remove_legacy_session_year_index(connection):
    """Upgrade existing databases to allow duplicate session dates."""
    if connection.dialect.name == "postgresql":
        exists = connection.execute(text(
            "SELECT to_regclass('uq_session_school_years') IS NOT NULL"
        )).scalar()
    elif connection.dialect.name == "sqlite":
        exists = connection.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'uq_session_school_years' AND tbl_name = 'sessions'"
        )).scalar()
    else:
        return
    if exists:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(731904219)"))
        connection.execute(text("DROP INDEX IF EXISTS uq_session_school_years"))


def ensure_session_table(connection):
    """Create the session table if absent, serializing PostgreSQL workers."""
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(731904219)"))
    SchoolSession.__table__.create(bind=connection, checkfirst=True)
    remove_legacy_session_year_index(connection)


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

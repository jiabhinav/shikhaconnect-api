from database.advisory_lock import acquire_transaction_lock
from fastapi import HTTPException
from sqlalchemy import text

from models.session import Session as SchoolSession
from models.session_month import SessionMonth


def replace_session_months(db, session):
    """Replace stored payment months for a session within the caller's transaction."""
    year, month = session.start_date.year, session.start_date.month
    wanted = {}
    while (year, month) <= (session.end_date.year, session.end_date.month):
        label = f"{year:04d}-{month:02d}"
        wanted[label] = label
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    duplicates = db.query(SessionMonth).filter(
        SessionMonth.school_id == session.school_id,
        SessionMonth.month.in_(wanted),
        SessionMonth.session_id != session.id,
    ).all() if wanted else []
    duplicate_months = {row.month for row in duplicates}
    # Include older sessions created before session_months was introduced.
    other_sessions = db.query(SchoolSession).filter(
        SchoolSession.school_id == session.school_id,
        SchoolSession.id != session.id,
    ).all()
    for other in other_sessions:
        year, month = other.start_date.year, other.start_date.month
        while (year, month) <= (other.end_date.year, other.end_date.month):
            label = f"{year:04d}-{month:02d}"
            if label in wanted:
                duplicate_months.add(label)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if duplicate_months:
        duplicate_months = sorted(duplicate_months)
        raise HTTPException(status_code=409, detail={
            "message": "One or more months already belong to another session for this school",
            "duplicate_months": duplicate_months,
        })
    # Regenerate IDs as well as month values so this behaves as a complete
    # replacement whenever the session form or explicit months API is saved.
    db.query(SessionMonth).filter_by(
        session_id=session.id, school_id=session.school_id
    ).delete(synchronize_session=False)
    db.flush()
    db.add_all(
        SessionMonth(school_id=session.school_id, session_id=session.id, month=label)
        for label in wanted
    )


# Backward-compatible name for callers outside this repository.
sync_session_months = replace_session_months


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
            acquire_transaction_lock(connection, 731904219)
        connection.execute(text("DROP INDEX IF EXISTS uq_session_school_years"))


def ensure_session_table(connection):
    """Create the session table if absent, serializing PostgreSQL workers."""
    if connection.dialect.name == "postgresql":
        acquire_transaction_lock(connection, 731904219)
    SchoolSession.__table__.create(bind=connection, checkfirst=True)
    remove_legacy_session_year_index(connection)


def validate_session_dates(db, school_id, start_date, end_date, exclude_id=None):
    """Reject overlapping inclusive date ranges within the same school."""
    with db.no_autoflush:
        query = db.query(SchoolSession.id).filter(
            SchoolSession.school_id == school_id,
            SchoolSession.start_date <= end_date,
            SchoolSession.end_date >= start_date,
        )
        if exclude_id is not None:
            query = query.filter(SchoolSession.id != exclude_id)
        if query.first() is not None:
            raise HTTPException(status_code=409, detail={
                "message": "Your session dates overlap another session already available for this school",
                "school_id": school_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            })


def update_school_session(db, school, school_info, session_id):
    """Update only the session identified by both its ID and school ID."""
    ensure_session_table(db.connection())
    session = db.query(SchoolSession).filter_by(id=session_id, school_id=school.id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found for this school")
    validate_session_dates(db, school.id, school_info.session_start_date,
        school_info.session_end_date, session.id)
    session.name = school_info.session_name
    session.start_date = school_info.session_start_date
    session.end_date = school_info.session_end_date

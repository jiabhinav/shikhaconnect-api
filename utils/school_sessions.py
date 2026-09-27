from models.session import Session as SchoolSession
from utils.dates import today
from utils.session_status import SessionStatus


def current_session_ids(db, school_ids, *, as_of=None):
    """Select each school's active session, matching the catalog backfill rule."""
    result = dict.fromkeys(school_ids)
    if not result:
        return result
    current_date = as_of if as_of is not None else today()
    rows = db.query(SchoolSession.school_id, SchoolSession.id).filter(
        SchoolSession.school_id.in_(result),
        SessionStatus.is_current(SchoolSession.start_date, SchoolSession.end_date, as_of=current_date),
    ).order_by(SchoolSession.start_date.desc(), SchoolSession.id.desc()).all()
    for school_id, session_id in rows:
        if result[school_id] is None:
            result[school_id] = session_id
    return result

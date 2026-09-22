from fastapi import HTTPException
from models.session import Session as SchoolSession


def require_school_session(db, school_id, session_id):
    if db.query(SchoolSession.id).filter_by(id=session_id, school_id=school_id).first() is None:
        raise HTTPException(404, "School session not found")

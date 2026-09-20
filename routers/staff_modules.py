from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import MetaData, Table, select
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.user import User

router = APIRouter(prefix="/staff-modules")


@router.get("")
def get_staff_modules(
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    try:
        modules = Table("staff_modules", MetaData(), autoload_with=db.connection())
    except NoSuchTableError as exc:
        raise HTTPException(status_code=503, detail="Staff modules table is unavailable") from exc

    query = select(modules)
    if "status" in modules.c:
        query = query.where(modules.c.status.is_(True))
    elif "is_active" in modules.c:
        query = query.where(modules.c.is_active.is_(True))
    else:
        raise HTTPException(status_code=503, detail="Staff modules active status column is unavailable")
    if "id" in modules.c:
        query = query.order_by(modules.c.id)
    return {
        "status": "success",
        "message": "Staff modules fetched successfully",
        "data": [dict(row) for row in db.execute(query).mappings()],
    }

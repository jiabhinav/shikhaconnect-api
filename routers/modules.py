from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import MetaData, Table, select
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.user import User

router = APIRouter(prefix="/modules")


@router.get("")
def get_modules(
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    try:
        modules = Table("modules", MetaData(), autoload_with=db.connection())
    except NoSuchTableError as exc:
        raise HTTPException(status_code=503, detail="Modules table is unavailable") from exc

    query = select(modules)
    if "id" in modules.c:
        query = query.order_by(modules.c.id)
    return {
        "status": "success",
        "message": "Modules fetched successfully",
        "data": [dict(row) for row in db.execute(query).mappings()],
    }

import hashlib
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from dependencies.db import get_db_session
from models.user import User


security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db_session),
) -> User:
    if not credentials or not credentials.scheme or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")

    token = credentials.credentials

    users = db.query(User).all()
    for user in users:
        expected = hashlib.sha256(f"{user.id}:{user.mobile}:{user.email}:{user.password}".encode("utf-8")).hexdigest()
        if expected == token:
            return user

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

import hashlib
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from dependencies.db import get_db_session
from models.user import User, UserStatus
from models.staff import Staff


security = HTTPBearer()


def account_token(account):
    # Staff and user primary keys may overlap; keep their tokens distinct.
    prefix = "staff:" if isinstance(account, Staff) else ""
    return hashlib.sha256(
        f"{prefix}{account.id}:{account.mobile}:{account.email}:{account.password}".encode("utf-8")
    ).hexdigest()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db_session),
) -> User:
    if not credentials or not credentials.scheme or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")

    token = credentials.credentials

    users = db.query(User).all() + db.query(Staff).all()
    for user in users:
        expected = account_token(user)
        if expected == token:
            if user.status not in (UserStatus.ACTIVE, "Active"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is disabled",
                )
            return user

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

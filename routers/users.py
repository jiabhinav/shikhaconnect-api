from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from dependencies.db import get_db_session
from models.user import User
from schemas.user import UserCreate, UserRegisterResponse

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.post("/", response_model=UserRegisterResponse, status_code=status.HTTP_200_OK)
def create_user(user: UserCreate, db: Session = Depends(get_db_session)):
    existing = db.query(User).filter(User.email == user.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    new_user = User(
        name=user.name,
        email=user.email,
        mobile=user.mobile,
        school_name=user.school_name,
        role=user.role,
        status=user.status,
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except Exception:
        db.rollback()
        raise

    response_data = {
        "id": new_user.id,
        "name": new_user.name,
        "email": str(new_user.email),
        "mobile": new_user.mobile,
        "school_name": new_user.school_name,
        "role": new_user.role.value if hasattr(new_user.role, "value") else str(new_user.role),
        "status": new_user.status.value if hasattr(new_user.status, "value") else str(new_user.status),
    }

    return {
        "status": "success",
        "message": "User registered successfully",
        "data": response_data,
    }
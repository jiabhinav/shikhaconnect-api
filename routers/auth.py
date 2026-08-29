import hashlib

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from dependencies.db import get_db_session
from models.user import User, UserStatus
from schemas.user import UserCreate, UserLogin, UserLoginResponse, UserRegisterResponse

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


def _build_user_payload(user: User):
    return {
        "id": user.id,
        "first_name": user.first_name,
        "middle_name": user.middle_name,
        "last_name": user.last_name,
        "email": str(user.email),
        "mobile": user.mobile,
        "password": user.password,
        "date_of_birth": user.date_of_birth,
        "designation": user.designation,
        "aadhaar_number": user.aadhaar_number,
        "nationality": user.nationality,
        "spouse_name": user.spouse_name,
        "father_name": user.father_name,
        "mother_name": user.mother_name,
        "description": user.description,
        "gender": user.gender,
        "line_1": user.line_1,
        "line_2": user.line_2,
        "city": user.city,
        "country": user.country,
        "state": user.state,
        "pin_code": user.pin_code,
        "school_name": user.school_name,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
    }


@router.post("/login", response_model=UserLoginResponse, status_code=status.HTTP_200_OK)
def login(credentials: UserLogin, db: Session = Depends(get_db_session)):
    user = db.query(User).filter(User.mobile == credentials.mobile).first()
    if not user or user.password != credentials.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid mobile number or password",
        )

    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    token = hashlib.sha256(
        f"{user.id}:{user.mobile}:{user.email}:{user.password}".encode("utf-8")
    ).hexdigest()

    payload = _build_user_payload(user)

    return JSONResponse(
        content={
            "status": "success",
            "message": "Login successful",
            "data": payload,
            "token": token,
            "token_type": "bearer",
        },
        headers={"Authorization": f"Bearer {token}"},
    )


@router.post("/register", response_model=UserRegisterResponse, status_code=status.HTTP_200_OK)
def register_auth(user: UserCreate, db: Session = Depends(get_db_session)):
    existing_email = db.query(User).filter(User.email == user.email).first()
    existing_mobile = db.query(User).filter(User.mobile == user.mobile).first()

    if existing_email or existing_mobile:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "status": "failed",
                "message": "Email or mobile already exists",
            },
        )

    new_user = User(
        first_name=user.first_name,
        middle_name=user.middle_name,
        last_name=user.last_name,
        email=user.email,
        mobile=user.mobile,
        password=user.password or user.mobile,
        date_of_birth=user.date_of_birth,
        designation=user.designation,
        aadhaar_number=user.aadhaar_number,
        nationality=user.nationality,
        spouse_name=user.spouse_name,
        father_name=user.father_name,
        mother_name=user.mother_name,
        description=user.description,
        gender=user.gender,
        line_1=user.line_1,
        line_2=user.line_2,
        city=user.city,
        country=user.country,
        state=user.state,
        pin_code=user.pin_code,
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
        "first_name": new_user.first_name,
        "middle_name": new_user.middle_name,
        "last_name": new_user.last_name,
        "email": str(new_user.email),
        "mobile": new_user.mobile,
        "password": new_user.password,
        "date_of_birth": new_user.date_of_birth,
        "designation": new_user.designation,
        "aadhaar_number": new_user.aadhaar_number,
        "nationality": new_user.nationality,
        "spouse_name": new_user.spouse_name,
        "father_name": new_user.father_name,
        "mother_name": new_user.mother_name,
        "description": new_user.description,
        "gender": new_user.gender,
        "line_1": new_user.line_1,
        "line_2": new_user.line_2,
        "city": new_user.city,
        "country": new_user.country,
        "state": new_user.state,
        "pin_code": new_user.pin_code,
        "school_name": new_user.school_name,
        "role": new_user.role.value if hasattr(new_user.role, "value") else str(new_user.role),
        "status": new_user.status.value if hasattr(new_user.status, "value") else str(new_user.status),
    }

    return {
        "status": "success",
        "message": "User registered successfully",
        "data": response_data,
    }

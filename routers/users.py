from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from dependencies.db import get_db_session
from dependencies.auth import get_current_user
from models.user import User, UserRole, UserStatus
from schemas.user import UserCreate, UserRegisterResponse, UserStatusUpdate, UserUpdate

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


def _require_super_admin(current_user: User) -> None:
    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value != UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Super Admin can enable or disable users",
        )


def _set_user_status(user_id: int, new_status: UserStatus, db: Session, current_user: User):
    _require_super_admin(current_user)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == current_user.id and new_status == UserStatus.INACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot disable your own account",
        )

    user.status = new_status
    try:
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        raise

    return {
        "status": "success",
        "message": f"User {'enabled' if new_status == UserStatus.ACTIVE else 'disabled'} successfully",
        "data": {
            "id": user.id,
            "status": user.status.value,
        },
    }


@router.patch("/{user_id}/status", status_code=status.HTTP_200_OK)
def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    new_status = UserStatus.ACTIVE if payload.status else UserStatus.INACTIVE
    return _set_user_status(user_id, new_status, db, current_user)


@router.post("/", response_model=UserRegisterResponse, status_code=status.HTTP_200_OK)
def create_user(user: UserCreate, db: Session = Depends(get_db_session)):
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


@router.get("/{user_id}", status_code=status.HTTP_200_OK)
def get_user(user_id: int, db: Session = Depends(get_db_session)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    response_data = {
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

    return {
        "status": "success",
        "message": "User fetched successfully",
        "data": response_data,
    }


@router.get("/", status_code=status.HTTP_200_OK)
def list_or_get_current_user(
    db: Session = Depends(get_db_session), current_user: User = Depends(get_current_user)
):
    # If Super Admin, return list of all users
    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value == UserRole.SUPER_ADMIN.value:
        users = db.query(User).all()
        data = []
        for user in users:
            data.append(
                {
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
            )

        return {"status": "success", "message": "Users fetched successfully", "data": data}

    # Otherwise, return only the current user's data
    user = current_user
    response_data = {
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

    return {"status": "success", "message": "User fetched successfully", "data": response_data}



@router.put("/{user_id}", response_model=UserRegisterResponse, status_code=status.HTTP_200_OK)
def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value != UserRole.SUPER_ADMIN.value and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update this user")

    update_data = user_update.dict(exclude_unset=True)

    if "status" in update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use the status endpoint to change user status",
        )

    # Unique field checks
    if "email" in update_data:
        existing = db.query(User).filter(User.email == update_data["email"], User.id != user_id).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")
    if "mobile" in update_data:
        existing = db.query(User).filter(User.mobile == update_data["mobile"], User.id != user_id).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mobile already exists")

    for key, value in update_data.items():
        setattr(user, key, value)

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        raise

    response_data = {
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

    return {"status": "success", "message": "User updated successfully", "data": response_data}


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
def delete_user(
    user_id: int, db: Session = Depends(get_db_session), current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value != UserRole.SUPER_ADMIN.value and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this user")

    try:
        db.delete(user)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {"status": "success", "message": "User deleted successfully"}

from utils.passwords import hash_password

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from dependencies.db import get_db_session
from dependencies.auth import get_current_user
from models.staff import Staff
from models.school import School
from models.school_mapping import SchoolMapping
from models.user import LoginUser, User, UserAddress, UserRole, UserStatus
from schemas.user import UserAssignedSchool, UserDetailResult, UserListResult, UserCreate, UserRegisterResponse, UserResponse, UserStatusUpdate, UserUpdate

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


def _set_login_user_status(login_user_id: int, new_status: UserStatus, db: Session, current_user: User):
    _require_super_admin(current_user)
    account = db.get(LoginUser, login_user_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Login account not found")

    if account.id == current_user.login_user_id and new_status == UserStatus.INACTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="You cannot disable your own account")

    account.status = new_status
    try:
        db.commit()
        db.refresh(account)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Account data conflicts with an existing record")
    except Exception:
        db.rollback()
        raise
    return {
        "status": "success",
        "message": f"Login account {'enabled' if new_status == UserStatus.ACTIVE else 'disabled'} successfully",
        "data": {"id": account.id, "status": account.status.value},
    }


@router.patch("/{login_user_id}/status", status_code=status.HTTP_200_OK)
def update_login_user_status(
    login_user_id: int,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Enable/disable any login account by login_user.id, including staff accounts."""
    new_status = UserStatus.ACTIVE if payload.status else UserStatus.INACTIVE
    return _set_login_user_status(login_user_id, new_status, db, current_user)


@router.post("/", response_model=UserRegisterResponse, status_code=status.HTTP_200_OK)
def create_user(user: UserCreate, db: Session = Depends(get_db_session)):
    existing_email = db.query(LoginUser).filter(LoginUser.email == user.email).first()
    existing_mobile = db.query(LoginUser).filter(LoginUser.mobile == user.mobile).first()

    if existing_email or existing_mobile:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "status": "failed",
                "message": "Email or mobile already exists",
            },
        )

    new_user = User(
        login_user=LoginUser(
            first_name=user.first_name,
            middle_name=user.middle_name,
            last_name=user.last_name,
            email=user.email,
            mobile=user.mobile,
            password=hash_password(user.password or user.mobile),
            role=user.role,
        ),
        date_of_birth=user.date_of_birth,
        designation=user.designation,
        aadhaar_number=user.aadhaar_number,
        nationality=user.nationality,
        spouse_name=user.spouse_name,
        father_name=user.father_name,
        mother_name=user.mother_name,
        description=user.description,
        gender=user.gender,
        address=UserAddress(
            line_1=user.line_1,
            line_2=user.line_2,
            city=user.city,
            country=user.country,
            state=user.state,
            pin_code=user.pin_code,
        ),
        status=user.status,
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Account data conflicts with an existing record")
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
        "role": new_user.role.value if hasattr(new_user.role, "value") else str(new_user.role),
        "status": new_user.status.value if hasattr(new_user.status, "value") else str(new_user.status),
    }

    return {
        "status": "success",
        "message": "User registered successfully",
        "data": response_data,
    }


def schools_by_user(db, user_ids):
    result = {user_id: [] for user_id in user_ids}
    if not result:
        return result
    assignments = db.query(SchoolMapping.user_id, School, SchoolMapping.status).join(
        School, School.id == SchoolMapping.school_id
    ).filter(SchoolMapping.user_id.in_(user_ids)).order_by(School.id).all()
    for user_id, school, mapping_status in assignments:
        result[user_id].append(UserAssignedSchool.model_validate(school).model_copy(
            update={"mapping_status": mapping_status}
        ).model_dump(mode="json"))
    return result


@router.get("/{user_id}", response_model=UserDetailResult, status_code=status.HTTP_200_OK)
def get_user(user_id: int, db: Session = Depends(get_db_session)):
    user = (
        db.query(User)
        .options(joinedload(User.login_user).joinedload(LoginUser.address))
        .filter(User.id == user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    data = UserResponse.model_validate(user).model_dump(mode="json")
    data["schools"] = schools_by_user(db, [user.id])[user.id]
    return {
        "status": "success",
        "message": "User fetched successfully",
        "data": data,
    }


@router.get("/", response_model=UserListResult, status_code=status.HTTP_200_OK)
def list_users(db: Session = Depends(get_db_session)):
    users = (
        db.query(User)
        .options(joinedload(User.login_user).joinedload(LoginUser.address))
        .order_by(User.id)
        .all()
    )
    assigned_schools = schools_by_user(db, [user.id for user in users])
    return {
        "status": "success",
        "message": "Users fetched successfully",
        "data": [
            {**UserResponse.model_validate(user).model_dump(mode="json"),
             "schools": assigned_schools[user.id]}
            for user in users
        ],
    }


@router.put("/{user_id}", response_model=UserRegisterResponse, status_code=status.HTTP_200_OK)
def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    user = db.query(User).options(joinedload(User.login_user).joinedload(LoginUser.address)).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value != UserRole.SUPER_ADMIN.value and (not isinstance(current_user, User) or current_user.id != user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update this user")

    update_data = user_update.model_dump(exclude_unset=True)
    if "password" in update_data:
        if update_data["password"] is None:
            update_data.pop("password")
        else:
            update_data["password"] = hash_password(update_data["password"])

    if "status" in update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use the status endpoint to change user status",
        )

    # Unique field checks
    if "email" in update_data:
        existing = db.query(LoginUser).filter(LoginUser.email == update_data["email"], LoginUser.id != user.login_user_id).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")
    if "mobile" in update_data:
        existing = db.query(LoginUser).filter(LoginUser.mobile == update_data["mobile"], LoginUser.id != user.login_user_id).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mobile already exists")

    login_fields = {"first_name", "middle_name", "last_name", "email", "mobile", "password", "role"}
    required_fields = login_fields - {"middle_name"}
    if any(key in required_fields and value is None for key, value in update_data.items()):
        raise HTTPException(status_code=400, detail="Required account fields cannot be null")

    address_fields = {"line_1", "line_2", "city", "country", "state", "pin_code"}
    if address_fields.intersection(update_data) and user.address is None:
        user.address = UserAddress()
    for key, value in update_data.items():
        if key in login_fields:
            target = user.login_user
        elif key in address_fields:
            target = user.address
        else:
            target = user
        setattr(target, key, value)

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Account data conflicts with an existing record")
    except Exception:
        db.rollback()
        raise

    return {
        "status": "success",
        "message": "User updated successfully",
        "data": UserResponse.model_validate(user).model_dump(mode="json"),
    }


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
def delete_user(
    user_id: int, db: Session = Depends(get_db_session), current_user: User = Depends(get_current_user)
):
    user = db.query(User).options(joinedload(User.login_user).joinedload(LoginUser.address)).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value == UserRole.SUPER_ADMIN.value and current_user.id == user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Super Admin cannot delete their own account")
    if role_value != UserRole.SUPER_ADMIN.value and (not isinstance(current_user, User) or current_user.id != user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this user")

    try:
        # Remove profile references before the account cascade. Shared schools
        # and module catalogs are not owned by this user.
        db.query(SchoolMapping).filter(SchoolMapping.user_id == user.id).delete(synchronize_session="fetch")
        db.query(Staff).filter(Staff.login_user_id == user.login_user_id).delete(synchronize_session="fetch")
        # User -> LoginUser cascades also remove its address and permissions.
        db.delete(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="User cannot be deleted while other records reference this account")
    except Exception:
        db.rollback()
        raise

    return {"status": "success", "message": "User deleted successfully"}

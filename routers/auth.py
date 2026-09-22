
from utils.passwords import hash_password, needs_rehash, verify_password
from utils.school_sessions import current_session_ids

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, selectinload

from dependencies.db import get_db_session
from database.module_names import get_module_names, get_staff_module_names
from dependencies.auth import account_token
from models.school import School
from models.school_assets import SchoolAssets
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.session import Session as SchoolSession
from models.user import LoginUser, User, UserRole, UserStatus
from models.staff import Staff
from schemas.user import LoginStaffPermission, LoginSchool, UserCreate, UserLogin, UserLoginResponse, UserRegisterResponse
from schemas.session import SessionResponse
from schemas.user import PasswordResetRequest, PasswordResetResponse

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


@router.post("/reset-password", response_model=PasswordResetResponse)
def reset_password(
    payload: PasswordResetRequest,
    db: Session = Depends(get_db_session),
):
    """Reset an account password by mobile number without an auth header."""
    user = db.query(Staff).filter(Staff.mobile == payload.mobile).first()
    if user is None:
        user = db.query(User).filter(User.mobile == payload.mobile).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password = hash_password(
        payload.new_password if payload.new_password is not None else user.mobile
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return PasswordResetResponse()


def _build_user_payload(user, *, include_address=True):
    payload = {
        "id": user.login_user_id,
        "first_name": user.first_name,
        "middle_name": user.middle_name,
        "last_name": user.last_name,
        "email": str(user.email),
        "mobile": user.mobile,
        "date_of_birth": user.date_of_birth.isoformat() if hasattr(user.date_of_birth, "isoformat") else user.date_of_birth,
        "designation": user.designation,
        "aadhaar_number": user.aadhaar_number,
        "nationality": user.nationality,
        "spouse_name": user.spouse_name,
        "father_name": user.father_name,
        "mother_name": user.mother_name,
        "description": getattr(user, "description", None),
        "gender": user.gender,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
    }
    if include_address:
        for field in ("line_1", "line_2", "city", "country", "state", "pin_code"):
            payload[field] = getattr(user.address, field, None)
    return payload


@router.post("/login", response_model=UserLoginResponse, status_code=status.HTTP_200_OK)
def login(credentials: UserLogin, db: Session = Depends(get_db_session)):
    account = db.query(LoginUser).filter(LoginUser.mobile == credentials.mobile).first()
    if account is None or not verify_password(credentials.password, account.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid mobile number or password",
        )

    if account.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.SUB_ADMIN):
        user = db.query(User).filter(User.login_user_id == account.id).first()
    else:
        # Staff profiles remain independent of the users table.
        user = db.query(Staff).filter(Staff.login_user_id == account.id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Login account has no linked profile")

    if user.status not in (UserStatus.ACTIVE, "Active"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not active yet, please contact the administrator",
        )

    payload = _build_user_payload(user, include_address=account.role != UserRole.SUPER_ADMIN)
    if user.role != UserRole.SUPER_ADMIN:
        if isinstance(user, Staff):
            schools = db.query(School, SchoolAssets.school_logo).outerjoin(
                SchoolAssets, SchoolAssets.school_id == School.id
            ).filter(School.id == user.school_id).all()
        else:
            assignments = db.query(SchoolMapping, School, SchoolAssets.school_logo).join(
                School, School.id == SchoolMapping.school_id
            ).outerjoin(
                SchoolAssets, SchoolAssets.school_id == School.id
            ).options(selectinload(School.permissions)).filter(
                SchoolMapping.user_id == account.id
            ).order_by(School.id).all()
            schools = [(school, logo) for mapping, school, logo in assignments
                       if mapping.status == SchoolMappingStatus.ACTIVE]
            if not assignments:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Still no school assigned for this user",
                )
        if not schools:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not active yet, please contact the administrator",
            )
        sessions_by_school = {school.id: [] for school, logo in schools}
        sessions = db.query(SchoolSession).filter(
            SchoolSession.school_id.in_(sessions_by_school)
        ).order_by(SchoolSession.start_date.desc(), SchoolSession.id.desc()).all()
        for session in sessions:
            sessions_by_school[session.school_id].append(SessionResponse.model_validate(session))
        payload["schools"] = []
        staff_permissions = None
        if account.role in (UserRole.ADMIN, UserRole.SUB_ADMIN):
            module_names = get_module_names(db, list({
                permission.module_id
                for school, logo in schools
                for permission in school.permissions
            }))
        else:
            module_names = get_staff_module_names(db, [p.staff_module_id for p in account.permissions])
            staff_permissions = [LoginStaffPermission(
                id=p.id, login_user_id=p.login_user_id, staff_module_id=p.staff_module_id,
                name=module_names.get(p.staff_module_id), is_enabled=p.is_enabled,
            ) for p in sorted(account.permissions, key=lambda permission: permission.id)]
        session_ids = current_session_ids(db, [school.id for school, _ in schools])
        for school, logo in schools:
            if staff_permissions is not None:
                # Do not read school-wide permissions for a staff account.
                school_data = LoginSchool.model_validate({
                    **{field: getattr(school, field) for field in LoginSchool.model_fields
                       if field not in {"permissions", "sessions", "school_logo", "current_session_id"}},
                    "permissions": staff_permissions,
                })
            else:
                school_data = LoginSchool.model_validate(school)
                school_data.permissions.sort(key=lambda permission: permission.id)
                for permission in school_data.permissions:
                    permission.name = module_names.get(permission.module_id)
            school_data.current_session_id = session_ids[school.id]
            school_data.school_logo = logo
            school_data.sessions = sessions_by_school[school.id]
            payload["schools"].append(school_data.model_dump(mode="json"))

    if needs_rehash(account.password):
        account.password = hash_password(credentials.password)
        try:
            db.commit()
            db.refresh(user)
        except Exception:
            db.rollback()
            raise

    token = account_token(user)

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
    # Use the same transaction and shared identity checks as /users/.
    from routers.users import create_user

    return create_user(user, db)

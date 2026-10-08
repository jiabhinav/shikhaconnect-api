from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from models.caste_category import CasteCategory
from models.teacher import Teacher, TeacherAddress, TeacherLogin
from models.user import UserStatus
from routers.staff import staff_school, staff_constraint_error
from schemas.teacher import TeacherWrite, TeacherResult, TeacherListResult, TeacherStatusUpdate
from utils.passwords import hash_password

router = APIRouter(prefix="/schools", tags=["Teachers"])


def teacher_record(db, school_id, teacher_id):
    item = db.query(Teacher).filter_by(id=teacher_id, school_id=school_id).first()
    if item is None:
        raise HTTPException(404, "Teacher not found")
    return item


def save_teacher(db, item, message):
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise staff_constraint_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return {"message": message, "data": item}


def apply_teacher(db, school_id, payload, item=None):
    values = payload.teacher_info.model_dump()
    category = db.query(CasteCategory.id).filter_by(id=values["caste_category_id"], school_id=school_id).first()
    if category is None:
        raise HTTPException(404, "caste_category_id does not exist in this school")
    values["mobile"] = values.pop("mobile_number")
    for field in ("mobile", "email"):
        if values[field] is None:
            continue
        query = db.query(TeacherLogin.id).filter(getattr(TeacherLogin, field) == values[field])
        if item is not None:
            query = query.filter(TeacherLogin.id != item.teacher_login_id)
        if query.first() is not None:
            raise HTTPException(409, f"{field.capitalize()} already exists")
    account_values = {field: values.pop(field) for field in
                      ("first_name", "middle_name", "last_name", "mobile", "email", "role")}
    if item is None:
        item = Teacher(school_id=school_id, teacher_login=TeacherLogin(
            school_id=school_id,
            **account_values, password=hash_password(account_values["mobile"])))
    else:
        for field, value in account_values.items():
            setattr(item.teacher_login, field, value)
    for field, value in values.items():
        setattr(item, field, value)
    address_values = payload.address.model_dump()
    if item.address is None:
        item.addresses.append(TeacherAddress(address_type="present", **address_values))
    else:
        for field, value in address_values.items():
            setattr(item.address, field, value)
    return item


@router.post("/teachers", response_model=TeacherResult, status_code=201)
def create_teacher(school_id: int, payload: TeacherWrite, db: Session = Depends(staff_school)):
    return save_teacher(db, apply_teacher(db, school_id, payload), "Teacher created successfully")


@router.get("/teachers", response_model=TeacherListResult)
def list_teachers(school_id: int, status: UserStatus | None = None, offset: int = Query(0, ge=0),
                  limit: int = Query(50, ge=1, le=200), db: Session = Depends(staff_school)):
    query = db.query(Teacher).join(TeacherLogin).filter(Teacher.school_id == school_id)
    if status is not None:
        query = query.filter(TeacherLogin.status == status)
    return {"message": "Teachers fetched successfully",
            "data": query.order_by(Teacher.id).offset(offset).limit(limit).all()}


@router.get("/teachers/{teacher_id}", response_model=TeacherResult)
def get_teacher(school_id: int, teacher_id: int, db: Session = Depends(staff_school)):
    return {"message": "Teacher fetched successfully", "data": teacher_record(db, school_id, teacher_id)}


@router.put("/teachers/{teacher_id}", response_model=TeacherResult)
def update_teacher(school_id: int, teacher_id: int, payload: TeacherWrite, db: Session = Depends(staff_school)):
    item = teacher_record(db, school_id, teacher_id)
    return save_teacher(db, apply_teacher(db, school_id, payload, item), "Teacher updated successfully")


@router.patch("/teachers/{teacher_id}/status", response_model=TeacherResult)
def update_teacher_status(school_id: int, teacher_id: int, payload: TeacherStatusUpdate,
                          db: Session = Depends(staff_school), current_user=Depends(get_current_user)):
    item = teacher_record(db, school_id, teacher_id)
    if isinstance(current_user, Teacher) and item.id == current_user.id and payload.status == "Inactive":
        raise HTTPException(400, "You cannot disable your own account")
    item.status = UserStatus(payload.status)
    return save_teacher(db, item, "Teacher status updated successfully")


@router.delete("/teachers/{teacher_id}")
def delete_teacher(school_id: int, teacher_id: int, db: Session = Depends(staff_school)):
    item = teacher_record(db, school_id, teacher_id)
    try:
        account = item.teacher_login
        db.delete(item)
        db.flush()
        db.delete(account)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Teacher is in use and cannot be deleted") from exc
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "message": "Teacher deleted successfully", "data": {"id": teacher_id}}

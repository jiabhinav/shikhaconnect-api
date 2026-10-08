"""Delete school-owned data in foreign-key order within the caller's transaction."""
from sqlalchemy import select

from database.database import Base
from database.table_init import register_models
from models.school import School
from models.session import Session as SchoolSession
from models.staff import Staff, StaffAddress, StaffPermission
from models.student import Student
from models.teacher import Teacher
from models.user import LoginUser, User
from models.school_mapping import SchoolMapping


def delete_school_data(db, school):
    register_models()
    school_id = school.id
    staff_account_ids = list(db.scalars(select(Staff.login_user_id).where(Staff.school_id == school_id)))
    teacher_ids = select(Teacher.id).where(Teacher.school_id == school_id)
    student_ids = select(Student.id).where(Student.school_id == school_id)
    session_ids = select(SchoolSession.id).where(SchoolSession.school_id == school_id)
    # Explicit deletions also handle databases with older RESTRICT constraints.
    for table in reversed(Base.metadata.sorted_tables):
        if table.name == School.__tablename__:
            continue
        if "school_id" in table.c:
            condition = table.c.school_id == school_id
        elif "student_id" in table.c:
            condition = table.c.student_id.in_(student_ids)
        elif "teacher_id" in table.c:
            condition = table.c.teacher_id.in_(teacher_ids)
        elif "session_id" in table.c:
            condition = table.c.session_id.in_(session_ids)
        else:
            continue
        db.execute(table.delete().where(condition))

    # Admin accounts are shared. Remove only orphaned accounts owned by staff.
    if staff_account_ids:
        orphan_ids = list(db.scalars(select(LoginUser.id).where(
            LoginUser.id.in_(staff_account_ids),
            ~select(User.id).where(User.login_user_id == LoginUser.id).exists(),
            ~select(Staff.id).where(Staff.login_user_id == LoginUser.id).exists(),
            ~select(SchoolMapping.id).where(SchoolMapping.user_id == LoginUser.id).exists(),
        )))
        for model in (StaffPermission, StaffAddress):
            db.execute(model.__table__.delete().where(model.login_user_id.in_(orphan_ids)))
        db.execute(LoginUser.__table__.delete().where(LoginUser.id.in_(orphan_ids)))
    db.execute(School.__table__.delete().where(School.id == school_id))
    db.expire_all()

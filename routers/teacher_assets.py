import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.teacher import Teacher
from models.user import UserRole
from routers.school_assets import asset_path, cleanup, validate_image
from utils.images import compress_image

router = APIRouter()
logger = logging.getLogger(__name__)


def teacher_assets_db(school_id: int, teacher_id: int,
                      db: Session = Depends(get_db_session), user=Depends(get_current_user)):
    role = getattr(user.role, 'value', user.role)
    allowed = role == UserRole.SUPER_ADMIN.value
    if role in (UserRole.ADMIN.value, UserRole.SUB_ADMIN.value):
        allowed = db.query(SchoolMapping.id).filter_by(
            school_id=school_id, user_id=getattr(user, 'login_user_id', None),
            status=SchoolMappingStatus.ACTIVE).first() is not None
    if not allowed:
        raise HTTPException(403, 'You cannot manage teacher files for this school')
    if db.query(Teacher.id).filter_by(id=teacher_id, school_id=school_id).first() is None:
        raise HTTPException(404, 'Teacher not found in this school')
    return db


def profile_result(request, school_id, teacher_id, relative):
    url = str(request.url_for('get_teacher_profile', teacher_id=teacher_id).include_query_params(
        school_id=school_id)) if relative else None
    return {'status': 'success', 'data': {'teacher_id': teacher_id,
                                         'profile_img': relative, 'profile_url': url}}


@router.post('/teachers/{teacher_id}/profile', status_code=201)
@router.put('/teachers/{teacher_id}/profile')
def upload_teacher_profile(request: Request, school_id: int, teacher_id: int,
                           file: UploadFile = File(...), db: Session = Depends(teacher_assets_db)):
    data, extension = validate_image(file)
    try:
        data = compress_image(data, extension, max_bytes=30_000)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    item = db.query(Teacher).filter_by(id=teacher_id, school_id=school_id).with_for_update(of=Teacher).one()
    prefix = f'{school_id}/teachers/{teacher_id}/profile/'
    relative = f'{prefix}{uuid4().hex}{extension}'
    path = asset_path(relative)
    old_path = item.profile_img
    # Build the response before saving so URL resolution cannot fail after commit.
    result = profile_result(request, school_id, teacher_id, relative)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        item.profile_img = relative
        db.commit()
    except OSError as exc:
        db.rollback()
        cleanup([relative])
        logger.exception("Teacher profile storage failed for school %s teacher %s", school_id, teacher_id)
        raise HTTPException(503, "Image storage is unavailable. Check upload folder permissions and disk space.") from exc
    except Exception:
        db.rollback()
        cleanup([relative])
        raise
    if old_path and old_path.startswith(prefix):
        cleanup([old_path])
    return result


@router.get('/teachers/{teacher_id}/profile', name='get_teacher_profile')
def get_teacher_profile(school_id: int, teacher_id: int, db: Session = Depends(teacher_assets_db)):
    item = db.query(Teacher).filter_by(id=teacher_id, school_id=school_id).one()
    if not item.profile_img or not asset_path(item.profile_img).is_file():
        raise HTTPException(404, 'Profile image not found')
    return FileResponse(asset_path(item.profile_img), headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.delete('/teachers/{teacher_id}/profile')
def delete_teacher_profile(request: Request, school_id: int, teacher_id: int,
                           db: Session = Depends(teacher_assets_db)):
    item = db.query(Teacher).filter_by(id=teacher_id, school_id=school_id).with_for_update(of=Teacher).one()
    old_path = item.profile_img
    try:
        item.profile_img = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    if old_path and old_path.startswith(f'{school_id}/teachers/{teacher_id}/profile/'):
        cleanup([old_path])
    return profile_result(request, school_id, teacher_id, None)

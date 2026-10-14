from enum import Enum
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school_mapping import SchoolMapping, SchoolMappingStatus
from models.student import Student
from models.student_document import StudentDocument
from models.user import UserRole
from routers.school_assets import asset_path, validate_image
from utils.images import compress_image

router = APIRouter()
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PROFILE_BYTES = 50_000


class DocumentType(str, Enum):
    BIRTH_CERTIFICATE = 'birth_certificate'
    AADHAAR = 'aadhaar'
    PREVIOUS_MARKSHEET = 'previous_marksheet'
    OTHER = 'other'


def student_assets_db(school_id: int, student_id: int, db: Session = Depends(get_db_session),
                      user=Depends(get_current_user)):
    role = getattr(user.role, 'value', user.role)
    allowed = role == UserRole.SUPER_ADMIN.value
    if role in (UserRole.ADMIN.value, UserRole.SUB_ADMIN.value):
        allowed = db.query(SchoolMapping.id).filter_by(school_id=school_id,
            user_id=getattr(user, 'login_user_id', None), status=SchoolMappingStatus.ACTIVE).first() is not None
    if not allowed:
        raise HTTPException(403, 'You cannot manage student files for this school')
    if db.query(Student.id).filter_by(id=student_id, school_id=school_id).first() is None:
        raise HTTPException(404, 'Student not found in this school')
    return db


def write_file(school_id, student_id, folder, prefix, extension, data):
    relative = f'{school_id}/students/{student_id}/{folder}/{prefix}_{uuid4().hex}{extension}'
    path = asset_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as output:
            output.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return relative


@router.put('/students/profile')
@router.post('/students/profile', status_code=201)
def upload_student_profile(school_id: int, student_id: int, file: UploadFile = File(...),
                           db: Session = Depends(student_assets_db)):
    data, extension = validate_image(file)
    try:
        data = compress_image(data, extension, max_bytes=MAX_PROFILE_BYTES)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    item = db.query(Student).filter_by(id=student_id, school_id=school_id).with_for_update().one()
    old_path = item.profile_img
    relative = write_file(school_id, student_id, 'profile', 'profile', extension, data)
    try:
        item.profile_img = relative
        db.commit()
    except Exception:
        db.rollback()
        asset_path(relative).unlink(missing_ok=True)
        raise
    if old_path and old_path.startswith(f'{school_id}/students/{student_id}/profile/'):
        asset_path(old_path).unlink(missing_ok=True)
    return {'status': 'success', 'data': {'student_id': student_id, 'profile_img': relative}}


@router.delete('/students/profile')
def delete_student_profile(school_id: int, student_id: int,
                           session_id: int = Query(..., gt=0),
                           db: Session = Depends(student_assets_db)):
    from dependencies.school_session import require_school_session
    require_school_session(db, school_id, session_id)
    item = db.query(Student).filter_by(
        id=student_id, school_id=school_id, session_id=session_id,
    ).with_for_update().first()
    if item is None:
        raise HTTPException(404, 'Student not found in this session')
    old_path = item.profile_img
    # Resolve the owned path before committing; remove the file only after saving.
    path = (asset_path(old_path) if old_path and
            old_path.startswith(f'{school_id}/students/{student_id}/profile/') else None)
    try:
        item.profile_img = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    if path is not None:
        path.unlink(missing_ok=True)
    return {'status': 'success', 'message': 'Student profile photo deleted successfully',
            'data': {'student_id': student_id, 'profile_img': ''}}


@router.get('/students/profile')
def get_student_profile(school_id: int, student_id: int, db: Session = Depends(student_assets_db)):
    item = db.get(Student, student_id)
    if not item.profile_img or not asset_path(item.profile_img).is_file():
        raise HTTPException(404, 'Profile image not found')
    return FileResponse(asset_path(item.profile_img), headers={'Cache-Control': 'private, no-store'})


@router.post('/students/documents', status_code=201)
def upload_student_document(school_id: int, student_id: int, document_type: DocumentType,
                            file: UploadFile = File(...), db: Session = Depends(student_assets_db)):
    data = file.file.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, 'Document exceeds 10 MB')
    if data.startswith(b'%PDF-'):
        extension, media_type = '.pdf', 'application/pdf'
    else:
        file.file.seek(0)
        data, extension = validate_image(file)
        media_type = {'.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp'}[extension]
    relative = write_file(school_id, student_id, 'documents', document_type.value, extension, data)
    try:
        item = StudentDocument(student_id=student_id, document_type=document_type.value,
                               path=relative, media_type=media_type)
        db.add(item)
        db.flush()
        result = {'id': item.id, 'document_type': item.document_type, 'path': relative}
        db.commit()
    except Exception:
        db.rollback()
        asset_path(relative).unlink(missing_ok=True)
        raise
    return {'status': 'success', 'data': result}


@router.get('/students/documents')
def list_student_documents(school_id: int, student_id: int, db: Session = Depends(student_assets_db)):
    items = db.query(StudentDocument).filter_by(student_id=student_id).order_by(StudentDocument.id).all()
    return {'status': 'success', 'data': [{'id': i.id, 'document_type': i.document_type, 'path': i.path} for i in items]}


@router.get('/students/documents/{document_id}')
def download_student_document(school_id: int, student_id: int, document_id: int,
                              db: Session = Depends(student_assets_db)):
    item = db.query(StudentDocument).filter_by(id=document_id, student_id=student_id).first()
    if item is None or not asset_path(item.path).is_file():
        raise HTTPException(404, 'Document not found')
    return FileResponse(asset_path(item.path), media_type=item.media_type,
                        filename=asset_path(item.path).name, headers={'Cache-Control': 'private, no-store'})

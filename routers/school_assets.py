import logging
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import warnings

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from dependencies.auth import get_current_user
from dependencies.db import get_db_session
from models.school import School
from models.school_assets import SchoolAssets
from models.user import User, UserRole
from schemas.school_assets import SchoolAssetField, SchoolAssetsResult

router = APIRouter()
STORAGE_ROOT = Path(__file__).resolve().parents[1] / "uploads" / "schools"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
TARGET_IMAGE_SIZE_KB = 250
TARGET_IMAGE_SIZE_BYTES = TARGET_IMAGE_SIZE_KB * 1024
FORMATS = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}
logger = logging.getLogger(__name__)


def compress_image(data, image_format):
    if len(data) <= TARGET_IMAGE_SIZE_BYTES:
        return data

    try:
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.copy()
            working = image
            for quality in (85, 75, 65, 55, 45, 35):
                buffer = BytesIO()
                if image_format in {"JPEG", "WEBP"}:
                    working = working.convert("RGB")
                    save_kwargs = {"format": image_format, "quality": quality, "optimize": True}
                    if image_format == "WEBP":
                        save_kwargs["method"] = 6
                    working.save(buffer, **save_kwargs)
                else:
                    working.save(buffer, format="PNG", optimize=True, compress_level=9)

                if len(buffer.getvalue()) <= TARGET_IMAGE_SIZE_BYTES:
                    return buffer.getvalue()

                if working.width > 1200 or working.height > 1200:
                    working = working.resize(
                        (max(1, int(working.width * 0.85)), max(1, int(working.height * 0.85))),
                        Image.Resampling.LANCZOS,
                    )

            final_buffer = BytesIO()
            if image_format in {"JPEG", "WEBP"}:
                working = working.convert("RGB")
                save_kwargs = {"format": image_format, "quality": 35, "optimize": True}
                if image_format == "WEBP":
                    save_kwargs["method"] = 6
                working.save(final_buffer, **save_kwargs)
            else:
                working.save(final_buffer, format="PNG", optimize=True, compress_level=9)
            return final_buffer.getvalue()
    except Exception:
        logger.warning("Failed to compress uploaded image; using original file", exc_info=True)
    return data


def assets_db(school_id: int, db: Session = Depends(get_db_session),
              current_user: User = Depends(get_current_user)):
    if getattr(current_user.role, "value", current_user.role) != UserRole.SUPER_ADMIN.value:
        raise HTTPException(403, "Only Super Admin can manage school assets")
    # Serialize changes for a school, including the first assets insert.
    if db.query(School).filter_by(id=school_id).with_for_update().first() is None:
        raise HTTPException(404, "School not found")
    return db


def asset_path(value):
    path = (STORAGE_ROOT / value).resolve()
    if not path.is_relative_to(STORAGE_ROOT.resolve()):
        raise HTTPException(404, "Image not found")
    return path


def cleanup(values):
    for value in values:
        if value:
            try:
                asset_path(value).unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove replaced school asset")


def validate_image(upload):
    data = upload.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Each image must be at most 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                if image_format not in FORMATS:
                    raise HTTPException(422, "Images must be PNG, JPEG, or WebP")
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "Invalid image file") from exc

    compressed_data = compress_image(data, image_format)
    return compressed_data, FORMATS[image_format][0]


def result(school_id, item, message):
    data = {"school_id": school_id}
    for field in SchoolAssetField:
        value = getattr(item, field.value, None)
        data[field.value] = value
    return {"message": message, "data": data}


@router.get("/school/{school_id}/assets", response_model=SchoolAssetsResult)
def get_assets(school_id: int, db: Session = Depends(assets_db)):
    return result(school_id, db.get(SchoolAssets, school_id), "School assets fetched successfully")


@router.post("/school/{school_id}/assets", response_model=SchoolAssetsResult)
@router.put("/school/{school_id}/assets", response_model=SchoolAssetsResult)
def upload_assets(school_id: int, school_logo: UploadFile | None = File(None),
                  board_logo: UploadFile | None = File(None),
                  principal_signature: UploadFile | None = File(None),
                  exam_coordinator_signature: UploadFile | None = File(None),
                  db: Session = Depends(assets_db)):
    uploads = {"school_logo": school_logo, "board_logo": board_logo,
               "principal_signature": principal_signature,
               "exam_coordinator_signature": exam_coordinator_signature}
    validated = {field: validate_image(upload) for field, upload in uploads.items() if upload is not None}
    if not validated:
        raise HTTPException(422, "Upload at least one image")
    item = db.get(SchoolAssets, school_id) or SchoolAssets(school_id=school_id)
    new_files, old_files = [], []
    try:
        (STORAGE_ROOT / str(school_id)).mkdir(parents=True, exist_ok=True)
        for field, (data, extension) in validated.items():
            value = f"{school_id}/{field}_{uuid4().hex}{extension}"
            new_files.append(value)
            asset_path(value).write_bytes(data)
            old_files.append(getattr(item, field))
            setattr(item, field, value)
        db.add(item)
        db.commit()
    except Exception:
        db.rollback()
        cleanup(new_files)
        raise
    cleanup(old_files)
    return result(school_id, item, "School assets saved successfully")


@router.get("/school/{school_id}/assets/{asset_name}")
def download_asset(school_id: int, asset_name: SchoolAssetField, db: Session = Depends(assets_db)):
    item = db.get(SchoolAssets, school_id)
    value = getattr(item, asset_name.value, None)
    if not value or not asset_path(value).is_file():
        raise HTTPException(404, "Image not found")
    media_type = next(media for extension, media in FORMATS.values() if extension == Path(value).suffix)
    return FileResponse(asset_path(value), media_type=media_type,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/school/{school_id}/assets/{asset_name}", response_model=SchoolAssetsResult)
def delete_asset(school_id: int, asset_name: SchoolAssetField, db: Session = Depends(assets_db)):
    item = db.get(SchoolAssets, school_id)
    value = getattr(item, asset_name.value, None)
    if not value:
        raise HTTPException(404, "Image not found")
    setattr(item, asset_name.value, None)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    cleanup([value])
    return result(school_id, item, "School asset deleted successfully")

"""Reusable image compression independent of API routes."""
from io import BytesIO

from PIL import Image, ImageOps


def compress_image(data: bytes, extension: str, *, max_bytes: int = 50_000) -> bytes:
    """Compress validated JPEG/PNG/WebP bytes strictly below max_bytes.

    Keeps the output format and reduces dimensions when quality alone is
    insufficient. Raises ValueError if the requested limit cannot be met.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    image_format = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}.get(extension.lower())
    if image_format is None:
        raise ValueError("Supported image extensions: .jpg, .jpeg, .png, .webp")
    if len(data) < max_bytes:
        return data
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source)
        image.load()
        if image_format == 'JPEG':
            image = image.convert('RGB')
        while True:
            for quality in (85, 70, 55, 40):
                buffer = BytesIO()
                if image_format == 'PNG':
                    image.save(buffer, format='PNG', optimize=True, compress_level=9)
                else:
                    image.save(buffer, format=image_format, quality=quality, optimize=True)
                result = buffer.getvalue()
                if len(result) < max_bytes:
                    return result
                if image_format == 'PNG':
                    break
            if image.size == (1, 1):
                raise ValueError(f"Could not compress image below {max_bytes} bytes")
            image = image.resize((max(1, int(image.width * .75)), max(1, int(image.height * .75))),
                                 Image.Resampling.LANCZOS)



import unittest
from io import BytesIO

from fastapi import UploadFile
from PIL import Image

from routers.school_assets import validate_image


class SchoolAssetUploadCompressionTests(unittest.TestCase):
    def test_validate_image_compresses_large_images_to_target_size(self):
        image = Image.new("RGB", (4000, 3000), color=(255, 255, 255))
        image_bytes = BytesIO()
        image.save(image_bytes, format="JPEG", quality=95)
        upload = UploadFile(filename="large.jpg", file=BytesIO(image_bytes.getvalue()))

        compressed, extension = validate_image(upload)

        self.assertEqual(extension, ".jpg")
        self.assertLessEqual(len(compressed), 250 * 1024)
        self.assertGreater(len(compressed), 0)

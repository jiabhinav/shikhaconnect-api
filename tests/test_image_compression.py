import io
import random
import unittest

from PIL import Image
from routers.school_assets import compress_image as compress_school_image
from utils.images import compress_image


class ImageCompressionTests(unittest.TestCase):
    def test_all_supported_formats_are_saved_strictly_below_30_kb(self):
        image = Image.frombytes('RGB', (400, 400), random.Random(42).randbytes(400 * 400 * 3))
        for image_format, extension in (('JPEG', '.jpg'), ('PNG', '.png'), ('WEBP', '.webp')):
            with self.subTest(format=image_format):
                stream = io.BytesIO()
                image.save(stream, format=image_format)
                data = stream.getvalue()
                self.assertGreater(len(data), 30_000)
                for output in (compress_image(data, extension), compress_school_image(data, image_format)):
                    self.assertLess(len(output), 30_000)
                    with Image.open(io.BytesIO(output)) as saved:
                        self.assertEqual(saved.format, image_format)
                        saved.verify()

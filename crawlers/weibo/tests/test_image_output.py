import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from weibo_spider.image_output import ImageOutput


class ImageOutputTest(unittest.TestCase):
    def test_resize_numbering_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            Image.new("RGBA", (100, 200), (0, 0, 0, 0)).save(source)
            output = ImageOutput("照片", 1024)
            with ThreadPoolExecutor(max_workers=3) as executor:
                targets = list(executor.map(lambda identity: output.save(source, folder, identity),
                                            ["one", "two", "three"]))
            self.assertEqual({target.name for target in targets},
                             {"照片_0001.jpg", "照片_0002.jpg", "照片_0003.jpg"})
            for target in targets:
                with Image.open(target) as image:
                    self.assertEqual(image.size, (512, 1024))
                    self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            resumed = ImageOutput("照片", 1024)
            self.assertTrue(resumed.existing(folder, "one"))
            self.assertEqual(resumed.save(source, folder, "four").name, "照片_0004.jpg")
            self.assertFalse(list(folder.glob("*.part")))

    def test_invalid_image_does_not_consume_number(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            source.write_bytes(b"invalid image")
            output = ImageOutput("test", 1024)
            with self.assertRaises(OSError):
                output.save(source, folder, "bad")
            Image.new("RGB", (2000, 1000)).save(source)
            self.assertEqual(output.save(source, folder, "good").name, "test_0001.jpg")

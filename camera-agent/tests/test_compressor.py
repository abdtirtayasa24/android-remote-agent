from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from camera_agent.compressor import InvalidCaptureError, normalize_jpeg


class NormalizeJpegTests(unittest.TestCase):
    def test_resizes_landscape_image_with_aspect_ratio_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source = directory / "source.png"
            destination = directory / "output.jpg"
            Image.new("RGB", (2000, 1000), "white").save(source)

            metadata = normalize_jpeg(
                source,
                destination,
                maximum_width=1280,
                maximum_height=720,
                quality=72,
            )

            self.assertEqual(
                (metadata.width_pixels, metadata.height_pixels), (1280, 640)
            )
            self.assertGreater(metadata.file_size_bytes, 0)
            self.assertEqual(len(metadata.sha256), 64)

            with Image.open(destination) as image:
                image.load()
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (1280, 640))

    def test_rejects_empty_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source = directory / "empty.jpg"
            destination = directory / "output.jpg"
            source.touch()

            with self.assertRaises(InvalidCaptureError):
                normalize_jpeg(
                    source,
                    destination,
                    maximum_width=1280,
                    maximum_height=720,
                    quality=72,
                )

            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()

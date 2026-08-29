import base64
from io import BytesIO
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from core.generated_images import (
    GeneratedImageError,
    has_visible_assistant_output,
    persist_generated_image,
)


class GeneratedImageTests(unittest.TestCase):
    @staticmethod
    def _encoded_image(image_format="PNG"):
        buffer = BytesIO()
        Image.new("RGB", (8, 6), (80, 100, 220)).save(buffer, format=image_format)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def test_persist_generated_image_uses_session_attachment_directory(self):
        with tempfile.TemporaryDirectory() as history_dir:
            part = persist_generated_image(
                history_dir,
                "session/one",
                "ig:one",
                self._encoded_image(),
            )

            self.assertEqual(part["type"], "output_image")
            self.assertEqual(part["mime_type"], "image/png")
            self.assertEqual(part["source_item_id"], "ig:one")
            self.assertTrue(os.path.isfile(part["path"]))
            self.assertIn(os.path.join("attachments", "session_one"), part["path"])
            self.assertTrue(has_visible_assistant_output("", [part]))

    def test_invalid_base64_is_explicit_and_creates_no_file(self):
        with tempfile.TemporaryDirectory() as history_dir:
            with self.assertRaisesRegex(GeneratedImageError, "base64"):
                persist_generated_image(history_dir, "session", "ig_bad", "not-base64")
            self.assertFalse(os.path.exists(os.path.join(history_dir, "attachments")))

    def test_unsupported_image_format_is_rejected(self):
        with tempfile.TemporaryDirectory() as history_dir:
            with self.assertRaisesRegex(GeneratedImageError, "不支持的格式"):
                persist_generated_image(
                    history_dir,
                    "session",
                    "ig_gif",
                    self._encoded_image("GIF"),
                )

    def test_encoded_size_limit_is_checked_before_decode(self):
        with tempfile.TemporaryDirectory() as history_dir:
            with patch("core.generated_images.MAX_GENERATED_IMAGE_BYTES", 3):
                with self.assertRaisesRegex(GeneratedImageError, "超过"):
                    persist_generated_image(
                        history_dir,
                        "session",
                        "ig_large",
                        "QUJDRA==",
                    )

    def test_atomic_replace_failure_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as history_dir:
            with patch("core.generated_images.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(GeneratedImageError, "disk full"):
                    persist_generated_image(
                        history_dir,
                        "session",
                        "ig_atomic",
                        self._encoded_image(),
                    )
            target_dir = os.path.join(history_dir, "attachments", "session")
            self.assertEqual(os.listdir(target_dir), [])


if __name__ == "__main__":
    unittest.main()

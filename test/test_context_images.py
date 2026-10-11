import base64
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from core.context_images import ImageBudgetUnavailable, image_token_budget
from core.long_context import estimate_request


class ImageContextBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "image.png")
        Image.new("RGB", (1024, 1024)).save(self.path)
        self.part = {"type": "input_image", "path": self.path, "detail": "auto"}

    def test_documented_model_rules_and_detail_levels(self):
        for model, detail, expected in (
            ("gpt-5.6-sol", "auto", 1229),
            ("gpt-5.6-sol", "low", 308),
            ("gpt-5.6-sol-2026-05-05", "high", 1229),
            ("gpt-4o", "high", 765),
            ("gpt-4o-mini", "high", 25501),
            ("gpt-4o-mini", "low", 2833),
            ("claude-sonnet-4-5", "auto", 1369),
        ):
            with self.subTest(model=model, detail=detail):
                self.assertEqual(image_token_budget(self.part, model, detail=detail), expected)
        Image.new("RGB", (2048, 2048)).save(self.path)
        self.assertEqual(image_token_budget(self.part, "gpt-5.6-sol", detail="high"), 3000)
        self.assertEqual(image_token_budget(self.part, "gpt-5.6-sol"), 4916)
        Image.new("RGB", (3840, 2160)).save(self.path)
        self.assertEqual(image_token_budget(self.part, "claude-opus-4-7"), 4784)

    def test_text_parts_are_counted_and_image_bytes_are_not_prose(self):
        message = {"role": "user", "content": "Describe this image", "content_parts": [self.part]}
        original = copy.deepcopy(message)
        local = estimate_request([message], [], model_name="gpt-5.6-sol")
        with open(self.path, "rb") as stream:
            data = "data:image/png;base64," + base64.b64encode(stream.read()).decode("ascii")
        inline = {"role": "user", "content": [{"type": "text", "text": message["content"]},
                  {"type": "image_url", "image_url": {"url": data, "detail": "auto"}}]}
        self.assertEqual(estimate_request([inline], [], model_name="gpt-5.6-sol"), local)
        self.assertGreater(local, 1229)
        self.assertEqual(message, original)
        plain = {"role": "user", "content": "Question", "content_parts": [{"type": "text", "text": "answer " * 100}]}
        self.assertGreater(estimate_request([plain], []), estimate_request([{"role": "user", "content": "Question"}], []))

    def test_user_auto_detail_and_tool_detail_match_existing_adapter(self):
        part = {**self.part, "detail": "low"}
        user = {"role": "user", "content": "image", "content_parts": [part]}
        tool = {"role": "tool", "content": "image", "content_parts": [part]}
        self.assertGreater(estimate_request([user], [], model_name="gpt-5.6-sol"),
                           estimate_request([tool], [], model_name="gpt-5.6-sol"))

    def test_unknown_rules_and_remote_dimensions_skip_only_image_estimation(self):
        with patch("core.context_images.Image.open") as read_image:
            with self.assertRaises(ImageBudgetUnavailable):
                image_token_budget(self.part, "unlisted-vision-model")
            read_image.assert_not_called()
        message = {"role": "user", "content": "visible text", "content_parts": [self.part]}
        stats = {}
        self.assertEqual(estimate_request([message], [], image_stats=stats),
                         estimate_request([{"role": "user", "content": "visible text"}], []))
        self.assertEqual(stats, {"image_tokens": 0, "unestimated_images": 1})
        self.assertEqual(message["content_parts"], [self.part])
        remote = {"type": "input_image", "image_url": "https://example.invalid/image.png"}
        with self.assertRaises(ImageBudgetUnavailable):
            image_token_budget(remote, "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()

import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from core.llm.providers import _build_openai_user_content
from core.long_context import estimate_request


class TestContextFileAttachments(unittest.TestCase):
    def test_estimate_matches_existing_file_delivery_without_mutating_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, data in (("recording.wav", b"RIFF\x00\xffWAVE"),
                               ("recording.mp3", b"ID3\x00\xff"),
                               ("report.pdf", b"%PDF-1.4"),
                               ("notes.txt", "需要统计的附件正文\n".encode("utf-8") * 2000),
                               ("large.csv", b"1,2,3\n" * 30000),
                               ("missing.docx", None)):
                with self.subTest(name=name):
                    path = os.path.join(directory, name)
                    if data is not None:
                        with open(path, "wb") as handle:
                            handle.write(data)
                    message = {"role": "user", "content": "处理文件", "content_parts": [
                        {"type": "input_file", "path": path, "name": name}]}
                    original = copy.deepcopy(message)
                    wire = _build_openai_user_content(message["content"], message["content_parts"])
                    expected = estimate_request([{"role": "user", "content": wire}], [])
                    self.assertEqual(estimate_request([message], []), expected)
                    self.assertGreater(expected, estimate_request([{"role": "user", "content": "处理文件"}], []))
                    self.assertEqual(message, original)
                    if name == "notes.txt":
                        self.assertGreater(expected, 18000)
                    else:
                        self.assertLess(expected, 500)

    def test_binary_files_are_not_opened_for_budgeting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "recording.wav")
            with open(path, "wb") as handle:
                handle.write(b"RIFF\xffWAVE")
            with patch("builtins.open", side_effect=AssertionError("binary payload must not be read")):
                self.assertGreater(estimate_request([{"role": "user", "content_parts": [
                    {"type": "input_file", "path": path}]}], []), 12)


if __name__ == "__main__":
    unittest.main()

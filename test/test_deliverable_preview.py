import os
import tempfile
import unittest

from core.deliverable_preview import (
    DELIVERABLE_TYPES,
    OFFICE_EXTENSIONS,
    iter_workspace_file_paths,
    linkify_workspace_paths_in_html,
    normalize_workspace_file,
    office_export_command,
    office_preview_cache_path,
)


class TestDeliverablePreviewHelpers(unittest.TestCase):
    def test_supports_modern_and_legacy_office_formats(self):
        for extension in (".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"):
            self.assertIn(extension, DELIVERABLE_TYPES)
            self.assertIn(extension, OFFICE_EXTENSIONS)
        self.assertIn(".md", DELIVERABLE_TYPES)
        self.assertIn(".markdown", DELIVERABLE_TYPES)

    def test_only_accepts_existing_workspace_files(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            inside_path = os.path.join(workspace, "交付 报告.pdf")
            outside_path = os.path.join(outside, "outside.pdf")
            with open(inside_path, "wb") as handle:
                handle.write(b"pdf")
            with open(outside_path, "wb") as handle:
                handle.write(b"pdf")

            self.assertEqual(normalize_workspace_file(inside_path, workspace), os.path.normpath(inside_path))
            self.assertEqual(normalize_workspace_file(outside_path, workspace), "")
            self.assertEqual(normalize_workspace_file(os.path.join(workspace, "missing.pdf"), workspace), "")

    def test_linkifies_paths_but_not_code_or_outside_files(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            report = os.path.join(workspace, "交付 报告.docx")
            external = os.path.join(outside, "external.docx")
            for path in (report, external):
                with open(path, "wb") as handle:
                    handle.write(b"office")
            source = f"<p>完成：{report}</p><code>{report}</code><p>{external}</p>"

            rendered = linkify_workspace_paths_in_html(source, workspace)

            self.assertEqual(rendered.count("cowork-file:"), 1)
            self.assertIn(f"<code>{report}</code>", rendered)
            self.assertIn(external, rendered)

    def test_finds_multiple_supported_paths(self):
        with tempfile.TemporaryDirectory() as workspace:
            paths = [os.path.join(workspace, "report.md"), os.path.join(workspace, "slides.ppt")]
            for path in paths:
                with open(path, "wb") as handle:
                    handle.write(b"x")
            matches = iter_workspace_file_paths("文件：" + "；".join(paths), workspace)
            self.assertEqual([item[2] for item in matches], [os.path.normpath(path) for path in paths])

    def test_office_preview_cache_changes_with_source_fingerprint(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as cache:
            source = os.path.join(workspace, "deck.pptx")
            with open(source, "wb") as handle:
                handle.write(b"one")
            first = office_preview_cache_path(source, cache)
            with open(source, "ab") as handle:
                handle.write(b"two")
            second = office_preview_cache_path(source, cache)
            self.assertNotEqual(first, second)
            self.assertTrue(first.endswith(".pdf"))

    def test_office_export_command_uses_helper_mode(self):
        command = office_export_command("input.doc", "output.pdf", main_script=__file__)
        self.assertIn("--office-preview-export", command)
        self.assertEqual(command[-2:], ["input.doc", "output.pdf"])


if __name__ == "__main__":
    unittest.main()

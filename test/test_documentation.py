import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT, "scripts", "check_docs.py")
SPEC = importlib.util.spec_from_file_location("check_docs", SCRIPT_PATH)
check_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_docs)


class DocumentationTests(unittest.TestCase):
    def test_canonical_document_links_and_versions(self):
        errors = []
        errors.extend(check_docs.validate_local_links(check_docs.markdown_files()))
        errors.extend(check_docs.validate_current_versions())
        errors.extend(check_docs.validate_legacy_paths_removed())
        self.assertEqual(errors, [])

    def test_current_version_allows_historical_references(self):
        path = check_docs.CURRENT_DOCS[0]
        text = f"当前应用版本：**{check_docs.APP_VERSION}**\n历史版本见 [5.2.0](releases/5.2.0.md)。"
        with patch.object(check_docs, "CURRENT_DOCS", (path,)), patch.object(check_docs, "_read", return_value=text):
            self.assertEqual(check_docs.validate_current_versions(), [])

    def test_current_version_rejects_stale_or_missing_declaration(self):
        path = check_docs.CURRENT_DOCS[0]
        for text in ("当前应用版本：**0.0.0**", f"正文中提到 {check_docs.APP_VERSION}，但没有版本声明。"):
            with self.subTest(text=text), patch.object(check_docs, "CURRENT_DOCS", (path,)), patch.object(check_docs, "_read", return_value=text):
                self.assertTrue(check_docs.validate_current_versions())

    def test_all_supported_version_labels(self):
        path = check_docs.CURRENT_DOCS[0]
        for label in ("当前应用版本：", "- 应用版本：", "适用版本：", "Current app version:"):
            with self.subTest(label=label), patch.object(check_docs, "CURRENT_DOCS", (path,)), patch.object(check_docs, "_read", return_value=f"{label} **{check_docs.APP_VERSION}**"):
                self.assertEqual(check_docs.validate_current_versions(), [])

    def test_links_images_and_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guide.md"
            (root / "target.md").write_text("# 使用说明\n", encoding="utf-8")
            (root / "screen.png").write_bytes(b"fixture")
            source.write_text("[说明](target.md#使用说明)\n![截图](screen.png)", encoding="utf-8")
            with patch.object(check_docs, "ROOT", root):
                self.assertEqual(check_docs.validate_local_links([source]), [])
                (root / "screen.png").unlink()
                errors = check_docs.validate_local_links([source])
                self.assertTrue(any("missing link target screen.png" in error for error in errors))
                source.write_text("[说明](target.md#不存在)\n[文件](missing.md)", encoding="utf-8")
                errors = check_docs.validate_local_links([source])
                self.assertTrue(any("missing heading" in error for error in errors))
                self.assertTrue(any("missing link target missing.md" in error for error in errors))

    def test_screenshot_scenarios_do_not_require_fixed_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "user-guide.md"
            (source.parent / "screen.png").write_bytes(b"fixture")
            with patch.object(check_docs, "ROOT", root), patch.object(check_docs, "markdown_files", return_value=[source]):
                for count in (1, 3):
                    source.write_text("## 1. 首次使用\n" + "![截图](screen.png)\n" * count, encoding="utf-8")
                    self.assertEqual(check_docs.validate_screenshot_contract(), [])
                source.write_text("## 1. 首次使用\n缺少操作图。", encoding="utf-8")
                self.assertTrue(any("missing scenario screenshot" in error for error in check_docs.validate_screenshot_contract()))

    def test_documentation_image_edits_do_not_unlock_runtime_assets(self):
        result = type("GitResult", (), {"returncode": 0, "stdout": "images/user-guide/old.png\nimages/ai-theme-visualize/old.png\nimages/app-icon.png\nskills/example/SKILL.md\nAGENTS.md\n", "stderr": ""})()
        with patch.object(check_docs.subprocess, "run", return_value=result):
            errors = check_docs.validate_protected_diff()
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("app-icon.png" in error for error in errors))
        self.assertTrue(any("SKILL.md" in error for error in errors))
        self.assertTrue(any("AGENTS.md" in error for error in errors))

    def test_product_concepts_and_screenshot_contract(self):
        errors = []
        errors.extend(check_docs.validate_product_concepts())
        errors.extend(check_docs.validate_screenshot_contract())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

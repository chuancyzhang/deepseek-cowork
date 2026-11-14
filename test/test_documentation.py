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
        errors.extend(check_docs.validate_document_assets())
        self.assertEqual(errors, [])

    def test_current_version_allows_historical_references(self):
        path = check_docs.CURRENT_DOCS[0]
        text = f"文档适用版本：**{check_docs.APP_VERSION}**\n历史版本见 [5.2.0](releases/5.2.0.md)。"
        with patch.object(check_docs, "CURRENT_DOCS", (path,)), patch.object(check_docs, "_read", return_value=text):
            self.assertEqual(check_docs.validate_current_versions(), [])

    def test_current_version_rejects_stale_or_missing_declaration(self):
        path = check_docs.CURRENT_DOCS[0]
        for text in ("文档适用版本：**0.0.0**", f"正文中提到 {check_docs.APP_VERSION}，但没有版本声明。"):
            with self.subTest(text=text), patch.object(check_docs, "CURRENT_DOCS", (path,)), patch.object(check_docs, "_read", return_value=text):
                self.assertTrue(check_docs.validate_current_versions())

    def test_all_supported_version_labels(self):
        path = check_docs.CURRENT_DOCS[0]
        for label in ("文档适用版本：", "Documented version:"):
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

    def test_images_are_optional_but_unused_assets_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "user-guide.md"
            (source.parent / "screen.png").write_bytes(b"fixture")
            (source.parent / "screen.png").unlink()
            (source.parent / "images").mkdir()
            (source.parent / "images" / "screen.png").write_bytes(b"fixture")
            with patch.object(check_docs, "ROOT", root), patch.object(check_docs, "CURRENT_DOCS", (source,)), patch.object(check_docs, "markdown_files", return_value=[source]):
                for count in (1, 3):
                    source.write_text("## 首次使用\n" + "![截图](images/screen.png)\n" * count, encoding="utf-8")
                    self.assertEqual(check_docs.validate_document_assets(), [])
                source.write_text("## 不需要图片的操作\n文字说明。", encoding="utf-8")
                self.assertTrue(any("unreferenced" in error for error in check_docs.validate_document_assets()))
                (source.parent / "images" / "screen.png").unlink()
                self.assertEqual(check_docs.validate_document_assets(), [])
                (source.parent / "images" / "validation.json").write_text("{}", encoding="utf-8")
                self.assertTrue(any("unexpected documentation artifact" in error for error in check_docs.validate_document_assets()))

    def test_documentation_image_edits_do_not_unlock_runtime_assets(self):
        result = type("GitResult", (), {"returncode": 0, "stdout": "images/user-guide/old.png\nimages/ai-theme-visualize/old.png\nimages/app-icon.png\nskills/example/SKILL.md\nAGENTS.md\n", "stderr": ""})()
        with patch.object(check_docs.subprocess, "run", return_value=result):
            errors = check_docs.validate_protected_diff()
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("app-icon.png" in error for error in errors))
        self.assertTrue(any("SKILL.md" in error for error in errors))
        self.assertTrue(any("AGENTS.md" in error for error in errors))

    def test_documented_release_can_precede_source_version(self):
        with patch.object(check_docs, "APP_VERSION", "999.0.0"):
            self.assertEqual(check_docs.validate_current_versions(), [])
        with patch.object(check_docs, "APP_VERSION", "0.0.0"):
            self.assertTrue(any("newer than app" in error for error in check_docs.validate_current_versions()))

    def test_references_reject_local_only_notes_and_missing_source_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guide.md"
            (root / "private.md").write_text("# Local only", encoding="utf-8")
            (root / "test").mkdir()
            (root / "test/current.py").write_text("", encoding="utf-8")
            source.write_text("[Local](private.md) `test/removed.py` `test/current.py`", encoding="utf-8")
            with patch.object(check_docs, "ROOT", root):
                errors = check_docs.validate_repository_references([source], {"guide.md", "test/current.py"})
                self.assertEqual(len(errors), 2)
                self.assertTrue(any("untracked file" in error for error in errors))
                self.assertTrue(any("test/removed.py" in error for error in errors))

    def test_historical_release_source_mentions_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "docs/releases/1.0.0.md"
            source.parent.mkdir(parents=True)
            source.write_text("旧入口 `scripts/retired.py`", encoding="utf-8")
            with patch.object(check_docs, "ROOT", root):
                self.assertEqual(check_docs.validate_repository_references([source], set()), [])


if __name__ == "__main__":
    unittest.main()

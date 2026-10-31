"""Writing-page round trips, persistence isolation and full-workspace navigation."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextDocument, QTextListFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.workbench import WorkbenchStore
from core.workspace_pages import page_target, page_markdown, create_page_file, read_page
from core.deliverable_editing import DeliverableEditError
from core.conversation_drafts import conversation_has_content, history_record_title
from ui.writing_page import WritingPage, document_markdown
from ui.primitives import ProductMessageBox


class WritingPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cowork-page-test-")
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch("core.deliverable_editing.get_app_data_dir", return_value=self.temp.name))
        self.store = WorkbenchStore()
        self.tab = self.store.open("one", "page", cwd=self.temp.name)
        self.pane = WritingPage(self.tab, self.temp.name)
        self.pane.resize(600, 500)
        self.pane.show()
        self.addCleanup(self.cleanup_pane)

    def cleanup_pane(self):
        self.wait_save()
        self.pane.close()
        self.pane.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait_save(self):
        for _ in range(500):
            self.app.processEvents()
            if self.pane.worker is None:
                return
            QTest.qWait(10)
        self.fail("Page save worker did not finish")

    def test_supported_formats_and_literal_markdown_round_trip(self):
        document = QTextDocument()
        cursor = QTextCursor(document)
        cursor.insertText("中文段落和 *原样* [文字] #内容")
        cursor.insertBlock()
        bold = QTextCharFormat(); bold.setFontWeight(QFont.Bold)
        italic = QTextCharFormat(); italic.setFontItalic(True)
        both = QTextCharFormat(bold); both.setFontItalic(True)
        cursor.insertText("加粗", bold)
        cursor.insertText(" 斜体 ", italic)
        cursor.insertText("两者", both)
        cursor.insertBlock()
        fmt = cursor.blockFormat(); fmt.setHeadingLevel(2); cursor.setBlockFormat(fmt)
        cursor.insertText("标题", QTextCharFormat())
        cursor.insertBlock()
        fmt.setHeadingLevel(0); cursor.setBlockFormat(fmt)
        listing = QTextListFormat(); listing.setStyle(QTextListFormat.ListDecimal); listing.setIndent(1)
        cursor.createList(listing)
        cursor.insertText("第一项")
        cursor.insertBlock(); cursor.insertText("第二项")
        cursor.insertBlock()
        cursor.currentList().remove(cursor.block())
        fmt = cursor.blockFormat(); fmt.setIndent(0); cursor.setBlockFormat(fmt)
        link = QTextCharFormat(); link.setAnchor(True); link.setAnchorHref("https://example.test/a")
        cursor.insertText("链接", link)
        markdown = document_markdown(document)
        for token in ("**加粗**", "*斜体*", "***两者***", "## 标题", "1. 第一项", "2. 第二项", "[链接](https://example.test/a)"):
            self.assertIn(token, markdown)
        restored = QTextDocument(); restored.setMarkdown(markdown)
        self.assertEqual(document_markdown(restored), markdown)
        self.assertEqual(restored.toPlainText(), document.toPlainText())

    def test_create_save_reopen_and_external_conflict_preserves_input(self):
        self.pane.title.setText("中文测试")
        self.pane.editor.setMarkdown("这是 **加粗** 和 *斜体*。\n\n- 项目一\n- 项目二")
        path = page_target(self.temp.name, "页面")
        self.assertTrue(self.pane.save(target=path))
        self.wait_save()
        self.assertFalse(self.pane.dirty)
        self.assertEqual(self.tab.path, path)
        self.assertEqual(self.tab.kind, "file")
        recovered = read_page(path)
        self.assertIn("**加粗**", recovered["body"])
        self.pane.editor.append("第二次保存")
        self.pane.save(); self.wait_save()
        self.assertIn("第二次保存", Path(path).read_text(encoding="utf-8"))
        Path(path).write_text("external", encoding="utf-8")
        self.pane.editor.append("保留草稿")
        before = self.pane.content()
        self.pane.save(); self.wait_save()
        self.assertTrue(self.pane.dirty)
        self.assertEqual(self.pane.content(), before)
        self.assertEqual(Path(path).read_text(), "external")
        self.assertIn("其他程序", self.pane.status.text())

    def test_create_is_non_overwriting_and_names_are_bounded(self):
        path = page_target(self.temp.name, "同名.md")
        create_page_file(path, b"first")
        with self.assertRaises(DeliverableEditError):
            create_page_file(path, b"second")
        self.assertEqual(Path(path).read_bytes(), b"first")
        for name in ("../escape", "C:\\file", "CON", "NUL.txt", "bad?name", "trailing."):
            with self.subTest(name=name), self.assertRaises(DeliverableEditError):
                page_target(self.temp.name, name)
        self.assertFalse(list(Path(self.temp.name).glob(".cowork-page-*")))

    def test_input_during_save_remains_dirty_and_recovery_is_exact(self):
        self.pane.title.setText("保存期间输入")
        self.pane.editor.setPlainText("第一版")
        ready, release = threading.Event(), threading.Event()
        original = create_page_file
        def slow(path, data):
            ready.set(); release.wait(5); original(path, data)
        with patch("ui.writing_page.create_page_file", side_effect=slow):
            self.pane.save(target=page_target(self.temp.name, "新页"))
            self.assertTrue(ready.wait(3))
            self.pane.editor.append("新输入")
            release.set(); self.wait_save()
        self.assertTrue(self.pane.dirty)
        self.assertNotIn("新输入", Path(self.tab.path).read_text(encoding="utf-8"))
        snapshot = self.pane.snapshot()
        recovered_tab = self.store.open("two", "file", path=self.tab.path)
        recovered = WritingPage(recovered_tab, self.temp.name, recovery=snapshot)
        try:
            self.assertEqual(recovered.content(), self.pane.content())
            self.assertTrue(recovered.dirty)
            self.assertEqual(recovered.tab.session_id, "two")
        finally:
            recovered.deleteLater()

    def test_page_recovery_counts_as_conversation_content(self):
        meta = {"workspace_pages": [{"title": "文章", "body": "正文"}]}
        self.assertTrue(conversation_has_content(meta))
        self.assertEqual(history_record_title({"has_messages": False, "title": "新任务", "meta": meta}), "页面 · 文章")

    def test_shutdown_waits_for_save_completion_before_final_snapshot(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QObject
        from ui.worker_lifecycle import pending_ui_workers
        host = QObject()
        host.browser_workbench = SimpleNamespace(panes={self.tab.id: self.pane})
        self.pane.editor.setPlainText("退出前保存")
        self.pane.save(target=page_target(self.temp.name, "退出前保存"))
        worker = self.pane.worker
        self.assertTrue(worker.wait(3000))
        # The native thread ended, but the GUI has not bound the file or updated
        # its recovery baseline yet. Shutdown must not capture stale metadata.
        self.assertIn(worker, pending_ui_workers(host))
        self.assertEqual(self.tab.path, "")
        self.wait_save()
        self.assertNotIn(worker, pending_ui_workers(host))
        self.assertTrue(self.tab.path)
        self.assertFalse(self.pane.dirty)

    def test_close_save_discard_cancel_and_write_failure(self):
        self.pane.editor.setPlainText("不会丢的草稿")
        closed = []
        with patch("ui.writing_page.ProductMessageBox.question", return_value=ProductMessageBox.Cancel):
            self.assertFalse(self.pane.can_close(lambda: closed.append(True)))
        with patch("ui.writing_page.ProductMessageBox.question", return_value=ProductMessageBox.Discard):
            self.assertTrue(self.pane.can_close(lambda: closed.append(True)))
        with patch("ui.writing_page.ProductMessageBox.question", return_value=ProductMessageBox.Save), \
             patch("ui.writing_page.ProductInputDialog.getText", return_value=("关闭前保存.md", True)), \
             patch("ui.writing_page.create_page_file", side_effect=PermissionError("文件夹不可写")):
            self.assertFalse(self.pane.can_close(lambda: closed.append(True)))
            self.wait_save()
        self.assertEqual(closed, [])
        self.assertEqual(self.pane.editor.toPlainText(), "不会丢的草稿")
        self.assertIn("内容已保留", self.pane.status.text())
        with patch("ui.writing_page.ProductMessageBox.question", return_value=ProductMessageBox.Save), \
             patch("ui.writing_page.ProductInputDialog.getText", return_value=("关闭前保存.md", True)):
            self.assertFalse(self.pane.can_close(lambda: closed.append(True)))
            self.wait_save(); self.app.processEvents()
        self.assertEqual(closed, [True])

    def test_save_shortcut_formatting_paste_undo_and_reopen(self):
        from PySide6.QtCore import QMimeData
        self.pane.title.setText("带 *符号* 的标题")
        source = QMimeData(); source.setText("中文和数字 123"); source.setHtml("<b>中文和数字 123</b>")
        self.pane.editor.insertFromMimeData(source)
        self.pane.editor.selectAll()
        self.pane.activateWindow(); self.pane.editor.setFocus()
        QTest.qWait(30)
        QTest.keyClick(self.pane.editor, Qt.Key_B, Qt.ControlModifier)
        self.assertIn("**中文和数字 123**", self.pane.content()["body"])
        self.pane.editor.undo()
        self.assertNotIn("**", self.pane.content()["body"])
        self.pane.editor.redo()
        with patch("ui.writing_page.ProductInputDialog.getText", return_value=("快捷键", True)):
            QTest.keyClick(self.pane.editor, Qt.Key_S, Qt.ControlModifier)
        self.wait_save()
        self.assertTrue(Path(self.temp.name, "快捷键.md").exists())
        self.assertFalse(self.pane.dirty)
        self.assertEqual(read_page(self.tab.path)["title"], self.pane.title.text())
        tab = self.store.open("reopened", "file", path=self.tab.path)
        reopened = WritingPage(tab, self.temp.name)
        try:
            self.assertEqual(reopened.content(), self.pane.content())
            self.assertFalse(reopened.dirty)
        finally:
            reopened.deleteLater()

    def test_recovery_error_is_visible_and_old_success_cannot_clear_it(self):
        self.pane.editor.setPlainText("恢复失败后仍可编辑")
        self.pane.recovery_result(8, "磁盘不可写")
        self.assertTrue(self.pane.recovery_notice.isVisible())
        self.pane.recovery_result(7)
        self.assertTrue(self.pane.recovery_notice.isVisible())
        self.pane.recovery_result(8)
        self.assertFalse(self.pane.recovery_notice.isVisible())
        self.assertEqual(self.pane.editor.toPlainText(), "恢复失败后仍可编辑")


class WorkspacePageIntegrationTests(unittest.TestCase):
    # Reuse the existing isolated MainWindow fixture and its full shutdown checks.
    from test_workspace_presentation import WorkspacePresentationTests as _Fixture
    setUpClass = classmethod(_Fixture.setUpClass.__func__)
    setUp = _Fixture.setUp
    tearDown = _Fixture.tearDown

    def test_expand_chat_restore_preserves_controls_and_drafts(self):
        window = self.window
        browser = window.browser_workbench
        window.show_context_drawer(window.RIGHT_TAB_FILES)
        window.input_field.setPlainText("聊天输入保留")
        input_field, session_tabs = window.input_field, window.session_tabs
        browser.new_page()
        tab = browser.active_tab()
        pane = browser.panes[tab.id]
        pane.title.setText("写作草稿")
        pane.editor.setPlainText("页面内容保留")
        QTest.qWait(50)
        split_width = window.right_sidebar.width()
        window.toggle_deliverable_preview_expanded()
        QTest.qWait(50)
        self.assertGreater(window.right_sidebar.width(), window.main_container.width() * .9)
        self.assertTrue(browser.chat_button.isVisible())
        self.assertFalse(window.content_area.isVisible())
        browser.chat_button.click(); QTest.qWait(50)
        self.assertTrue(window.content_area.isVisible())
        self.assertTrue(window.input_field.isVisible())
        self.assertFalse(window.right_stack.isVisible())
        self.assertIs(window.input_field, input_field)
        self.assertIs(window.session_tabs, session_tabs)
        self.assertEqual(window.input_field.toPlainText(), "聊天输入保留")
        self.assertEqual(browser.active_tab(), tab)
        window.toggle_deliverable_preview_expanded(); QTest.qWait(50)
        self.assertEqual(window.right_sidebar.width(), split_width)
        self.assertTrue(window.right_stack.isVisible())
        self.assertTrue(window.content_area.isVisible())
        self.assertEqual(pane.editor.toPlainText(), "页面内容保留")
        self.assertEqual(browser.submission_context(window.current_session_id)["tabs"], [])

    def test_recovery_is_session_scoped_and_saved_file_keeps_same_tab(self):
        window = self.window; browser = window.browser_workbench
        first = window.get_current_session()
        browser.new_page()
        tab = browser.active_tab()
        pane = browser.panes[tab.id]
        pane.title.setText("保留这页")
        pane.editor.setPlainText("正文")
        first_snapshot = browser.page_snapshots(first)
        self.assertEqual(first_snapshot[0]["body"], "正文")
        meta = window._compose_session_meta(first)
        self.assertEqual(meta["workspace_pages"], first_snapshot)
        second = window.create_new_session()
        second = window.get_current_session()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(browser.page_snapshots(second), [])
        window.set_current_session(first.session_id)
        self.assertIs(browser.panes[tab.id], pane)
        browser.activate(tab.id)
        path = str(Path(self.temp.name) / "saved.md")
        pane.save(target=path)
        for _ in range(500):
            QTest.qWait(10)
            if pane.worker is None: break
        self.assertIsNone(pane.worker)
        self.assertEqual(tab.path, path)
        self.assertEqual(len([t for t in browser.store.list(first.session_id) if t.path == path]), 1)
        self.assertIs(browser.stack.currentWidget(), pane)
        self.assertTrue(window.save_chat_history(first.session_id, flush=True))
        snapshots = browser.page_snapshots(first)
        browser.dispose_session(first.session_id)
        first.open_file_paths = []
        first.persisted_conversation_meta["workspace_pages"] = snapshots
        browser.restore_session(first)
        restored = browser.panes[browser.active_tab().id]
        self.assertEqual(restored.content()["body"], "正文")
        self.assertFalse(restored.dirty)
        window.show_context_drawer(window.RIGHT_TAB_FILES)
        window.set_workspace_expanded(True)
        window.select_workspace_conversation()
        self.assertFalse(browser.submission_context(first.session_id)["panel_visible"])
        browser.activate(restored.tab.id)
        self.assertTrue(browser.submission_context(first.session_id)["panel_visible"])
        browser.show_page_source(restored.tab)
        self.assertIs(browser.stack.currentWidget(), browser.file_surface)
        self.assertEqual(window.current_deliverable_path, path)


if __name__ == "__main__":
    unittest.main()

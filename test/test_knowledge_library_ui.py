import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.knowledge_library import KnowledgeService, KnowledgeStore
from core.theme import DesignTokens
from ui.knowledge_library import KnowledgePage
from test_knowledge_library import TestProtector, WeKnoraFixture


class KnowledgeLibraryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = KnowledgeStore(self.temp.name, TestProtector())
        self.transport = WeKnoraFixture()
        self.service = KnowledgeService(self.store, self.transport)
        self.service.login("http://localhost", "reader@example.test", "password")
        self.page = KnowledgePage(service=self.service, artifacts=lambda: [
            {"path": "D:/projects/product/report.docx", "project": "产品"},
            {"path": "D:/projects/research/data.xlsx", "project": "研究"}])
        self.page.resize(1100, 780)
        self.page.show()
        self.wait_for(lambda: self.page.tree.topLevelItemCount() == 6)

    def tearDown(self):
        self.page.jobs.pool.shutdown(wait=True)
        self.app.processEvents()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(predicate(), self.page.notice.text())

    def test_hover_checkbox_selects_without_opening_and_title_opens(self):
        from PySide6.QtWidgets import QStyleOptionViewItem, QStyle
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.wait_for(lambda: self.page.items.count() > 0)
        table = self.page.items
        item = table.item(0)
        index = table.indexFromItem(item)
        option = QStyleOptionViewItem()
        table.itemDelegate().initStyleOption(option, index)
        self.assertFalse(option.features & QStyleOptionViewItem.HasCheckIndicator)
        option.state |= QStyle.State_MouseOver
        table.itemDelegate().initStyleOption(option, index)
        self.assertTrue(option.features & QStyleOptionViewItem.HasCheckIndicator)
        activated = []
        table.fileActivated.connect(activated.append)
        point = table.checkbox_rect(item).center()
        QTest.mouseMove(table.viewport(), point)
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(item.checkState(0), Qt.Checked)
        self.assertFalse(activated)
        self.assertTrue(self.page.batch_bar.isVisible())
        self.page.check_page(False)
        self.assertFalse(self.page.batch_bar.isVisible())
        point.setX(table.visualRect(index).left() + 100)
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(activated, [item])

    def test_project_picker_filters_archived_and_searches(self):
        from ui.knowledge_library import KnowledgeProjectPicker
        picker = KnowledgeProjectPicker([
            {"name": "产品", "path": "D:/product"},
            {"name": "归档", "path": "D:/old", "archived": True},
            {"name": "隐藏", "path": "D:/hidden", "hidden": True},
            {"name": "研究", "path": "D:/research"},
        ])
        self.assertEqual(picker.projects.count(), 2)
        picker.search.setText("研究")
        self.assertTrue(picker.projects.item(0).isHidden())
        picker.projects.setCurrentRow(1)
        picker.choose()
        self.assertEqual(picker.selected_project["path"], "D:/research")
        picker.close()

    def test_local_html_preview_reads_source_without_upload_or_login(self):
        path = os.path.join(self.temp.name, "preview.html")
        with open(path, "wb") as source:
            source.write(b"<h1>Local preview</h1>")
        self.page.current_file = path
        with patch.object(self.page, "show_html") as render:
            self.page.preview_local_html(path)
            self.wait_for(lambda: render.called)
            self.assertIn(b"Local preview", render.call_args.args[0])

    def test_remote_html_uses_preview_during_indexing(self):
        self.transport.documents["doc-a"].update(title="report.html", parse_status="finalizing")
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.wait_for(lambda: self.page.items.count() == 1)
        with patch.object(self.page, "show_html") as render:
            self.page.select_item(self.page.items.item(0))
            self.wait_for(lambda: render.called)
            self.assertIn(b"Preview while indexing", render.call_args.args[0])

    def test_markdown_local_artifact_renders_without_remote_request(self):
        path = os.path.join(self.temp.name, "notes.md")
        with open(path, "w", encoding="utf-8") as source:
            source.write("# 本机资料\n\n正文可直接阅读")
        self.page.current_file = path
        self.page.preview_local_file(path)
        self.wait_for(lambda: "正文可直接阅读" in self.page.reader.toPlainText())
        self.assertNotIn("# 本机资料", self.page.reader.toPlainText())

    def test_upload_list_reconciles_completed_remote_record(self):
        path = os.path.join(self.temp.name, "upload.md")
        with open(path, "w") as source:
            source.write("test")
        task = self.service.upload(self.service.snapshot(), path, "kb-a", "")
        self.assertEqual(task["status"], "pending")
        self.page.show_uploads()
        self.wait_for(lambda: not self.page.upload_poll_busy)
        self.assertEqual(self.store.uploads()[0]["status"], "completed")
        self.assertEqual(self.page.items.item(0).text(3), "可检索")
        self.assertNotIn("等待解析", self.page.items.item(0).text())

    def test_reference_popover_has_transactional_selection(self):
        from ui.knowledge_library import KnowledgeReferencePopover
        references = [{"title": "资料甲"}, {"title": "资料乙"}]
        popover = KnowledgeReferencePopover(references, self.page)
        applied = []
        popover.applied.connect(applied.append)
        popover.items.item(0).setCheckState(Qt.Unchecked)
        self.assertEqual(references, [{"title": "资料甲"}, {"title": "资料乙"}])
        self.assertEqual(applied, [])
        popover.apply_selection()
        self.assertEqual(applied, [[{"title": "资料乙"}]])

    def test_pdf_and_image_local_previews(self):
        from pypdf import PdfWriter
        from PySide6.QtGui import QImage
        pdf_path = os.path.join(self.temp.name, "sample.pdf")
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=400)
        with open(pdf_path, "wb") as target:
            writer.write(target)
        self.page.current_file = pdf_path
        self.page.preview_local_file(pdf_path)
        self.assertIs(self.page.reader_stack.currentWidget(), self.page.pdf_view)
        image_path = os.path.join(self.temp.name, "sample.png")
        picture = QImage(100, 80, QImage.Format_RGB32)
        picture.fill(Qt.blue)
        picture.save(image_path)
        self.page.current_file = image_path
        self.page.preview_local_file(image_path)
        self.assertIs(self.page.reader_stack.currentWidget(), self.page.image_preview)
        self.assertFalse(self.page.image_preview.pixmap().isNull())

    def test_office_local_preview_reuses_structured_renderer(self):
        from docx import Document
        path = os.path.join(self.temp.name, "sample.docx")
        document = Document()
        document.add_paragraph("本地产物正文")
        document.save(path)
        self.page.current_file = path
        with patch.object(self.page, "show_html") as render:
            self.page.preview_local_file(path)
            self.wait_for(lambda: render.called)
            self.assertIn("本地产物正文", render.call_args.args[0].decode("utf-8"))

    def test_checked_documents_emit_one_batch_without_parent_library(self):
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.wait_for(lambda: self.page.items.count() == 1)
        self.page.add_item("第二份资料", {"document": {"id": "doc-b", "knowledge_base_id": "kb-a", "title": "第二份资料"}})
        self.page.check_page(True)
        captured = []
        self.page.referenceRequested.connect(lambda refs, mode: captured.append((refs, mode)))
        self.page.use_reference("new")
        self.assertEqual(len(captured), 1)
        self.assertEqual({r["knowledge_id"] for r in captured[0][0]}, {"doc-a", "doc-b"})
        self.assertEqual(self.page.content_stack.currentIndex(), 0)

    def test_batch_upload_retains_partial_failure_without_replay(self):
        from core.knowledge_library import KnowledgeError
        calls = []
        def upload(scope, path, kb, folder):
            calls.append(path)
            if path == "bad.md":
                raise KnowledgeError("outcome_unknown", "结果待核对")
            return {"status": "pending"}
        with patch.object(self.service, "upload", side_effect=upload):
            self.page.submit_upload_batch(self.service.snapshot(), ["one.md", "bad.md", "two.md"], "kb-a", "")
            self.wait_for(lambda: "2 / 3" in self.page.notice.text())
        self.assertEqual(calls, ["one.md", "bad.md", "two.md"])
        self.assertIn("bad.md", self.page.notice.text())

    def test_explicit_upload_survives_catalog_navigation(self):
        with patch.object(self.page, "choose_upload") as choose:
            self.page.upload_paths(["one.md", "two.md"])
            self.page.clear_view()
            self.wait_for(lambda: choose.called)
            self.assertEqual(len(choose.call_args.args[0]), 2)

    def test_catalog_browse_read_and_reference_signal(self):
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.wait_for(lambda: self.page.items.count() == 1)
        self.page.select_item(self.page.items.item(0))
        self.wait_for(lambda: "只读用户可以阅读" in self.page.reader.toPlainText())
        captured = []
        self.page.referenceRequested.connect(lambda ref, mode: captured.append((ref, mode)))
        self.page.use_reference("current")
        self.assertEqual(captured[0][0]["knowledge_id"], "doc-a")
        self.assertEqual(captured[0][1], "current")

    def test_reading_has_its_own_page_and_back_preserves_list(self):
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.wait_for(lambda: self.page.items.count() == 1)
        self.page.select_item(self.page.items.item(0))
        self.wait_for(lambda: "只读用户可以阅读" in self.page.reader.toPlainText())
        self.assertEqual(self.page.content_stack.currentIndex(), 1)
        self.assertFalse(self.page.search_toolbar.isVisible())
        self.page.back_to_list()
        self.assertEqual(self.page.content_stack.currentIndex(), 0)
        self.assertEqual(self.page.items.count(), 1)
        self.assertEqual(self.page.current_ref["kb_id"], "kb-a")
        self.assertFalse(self.page.current_ref.get("knowledge_id"))

    def test_search_result_can_be_read(self):
        self.page.query.setText("权限")
        self.page.search()
        self.wait_for(lambda: self.page.items.count() == 1)
        self.page.select_item(self.page.items.item(0))
        self.wait_for(lambda: "只读用户可以阅读" in self.page.reader.toPlainText())

    def test_wiki_can_be_read(self):
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.wait_for(lambda: self.page.items.count() == 1)
        self.page.show_wiki()
        self.wait_for(lambda: self.page.items.count() == 1 and "Wiki 编辑" in self.page.notice.text())
        self.page.select_item(self.page.items.item(0))
        self.wait_for(lambda: "产品概览正文" in self.page.reader.toPlainText())

    def test_wiki_groups_use_server_types_and_category_paths(self):
        self.transport.wiki_pages = [
            {"slug": "summary", "title": "原始文档摘要", "page_type": "summary"},
            {"slug": "concept", "title": "认知力", "page_type": "concept", "category_path": ["投资基础"]},
            {"slug": "index", "title": "Index", "page_type": "index"},
        ]
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.wait_for(lambda: self.page.items.count() == 1)
        self.page.show_wiki()
        self.wait_for(lambda: self.page.items.count() == 3)
        self.assertEqual(self.page.items.topLevelItem(0).text(), "概览与导航")
        self.assertEqual(self.page.items.topLevelItem(1).text(), "概念 · 投资基础")
        self.assertEqual(self.page.items.topLevelItem(2).text(), "文档摘要")
        self.assertTrue(self.page.items.isColumnHidden(3))
        self.assertTrue(self.page.files_button.isVisible())
        self.page.files_button.click()
        self.wait_for(lambda: self.page.view == "files" and self.page.items.count() == 1)
        self.assertFalse(self.page.items.isColumnHidden(3))

    def test_small_window_reflows_and_theme_can_be_reverted(self):
        self.page.resize(650, 760)
        self.app.processEvents()
        self.assertTrue(self.page.items.isColumnHidden(2))
        self.assertEqual(self.page.content_stack.currentIndex(), 0)
        before = self.page.styleSheet()
        original = DesignTokens.primary
        try:
            DesignTokens.primary = "#112233"
            self.page.refresh_theme()
            self.assertIn("#112233", self.page.styleSheet())
        finally:
            DesignTokens.primary = original
            self.page.refresh_theme()
        self.assertEqual(self.page.styleSheet(), before)
        self.assertLessEqual(self.page.use_button.geometry().right(), self.page.use_button.parentWidget().width())

    def test_stale_page_result_does_not_overwrite_navigation(self):
        self.page.open_kb({"id": "kb-a", "name": "产品资料"})
        self.page.navigate(self.page.tree.topLevelItem(1))
        self.page.jobs.pool.shutdown(wait=True)
        self.app.processEvents()
        self.assertEqual(self.page.title.text(), "本地产物")
        self.assertEqual(self.page.items.count(), 2)

    def test_artifact_project_filter(self):
        self.page.navigate(self.page.tree.topLevelItem(1))
        self.page.project_filter.setCurrentIndex(self.page.project_filter.findData("产品"))
        self.page.show_artifacts()
        self.assertEqual(self.page.items.count(), 1)
        self.assertIn("report.docx", self.page.items.item(0).text())

    def test_permission_failure_is_visible(self):
        self.transport.fail_status = 403
        self.page.query.setText("权限")
        self.page.search()
        self.wait_for(lambda: "无权" in self.page.notice.text())
        self.assertEqual(self.page.items.count(), 0)

    def test_offline_artifacts_still_visible(self):
        self.service.logout()
        self.page.refresh()
        self.assertTrue(self.page.login_box.isVisible())
        self.page.navigate(self.page.tree.topLevelItem(0))
        self.assertEqual(self.page.items.count(), 2)


if __name__ == "__main__":
    unittest.main()

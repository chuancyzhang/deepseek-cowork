import os
import tempfile
import time
import unittest

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

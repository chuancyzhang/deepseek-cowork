import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QScrollArea
from core.connections.broker import ConnectionBroker
from core.connections.store import ConnectionStore
from core.connections.templates import validate_template
from core.theme import DesignTokens
from ui.account_connections import AccountConnectionsPage
from test_connections import FakeAdapter, Protector, template


class ConnectionsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConnectionStore(self.temp.name, Protector())
        self.broker = ConnectionBroker(self.temp.name, self.temp.name, store=self.store, adapters=FakeAdapter)
        self.broker.catalog.write(self.broker.catalog.distributed_path, [template("weknora", service="weknora")])
        self.page = AccountConnectionsPage(broker=self.broker)
        self.page.resize(520, 760)
        self.page.show()
        self.app.processEvents()
        self.addCleanup(self.close_page)

    def close_page(self):
        self.page.cancel_login()
        self.page.jobs.pool.shutdown(wait=True)
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def test_empty_page_offers_template_and_no_login_gate(self):
        self.assertEqual(self.page.templates.count(), 1)
        self.assertTrue(self.page.add_button.isEnabled())
        self.assertFalse(self.page.login_button.isEnabled())
        self.assertFalse(self.store.exists)

    def test_add_has_service_specific_inputs(self):
        self.page.add()
        self.assertEqual(set(self.page.fields), {"username", "password"})
        self.assertIn("未配置", self.page.details.text())
        self.assertFalse(self.page.requirement_id.isVisible())

    def test_failed_login_keeps_input_and_legacy_mode(self):
        self.page.add()
        self.page.fields["username"].setText("person@example.test")
        self.page.fields["password"].setText("secret-test")
        from core.connections.errors import ConnectionError
        with patch.object(self.broker, "authenticate", side_effect=ConnectionError("authentication_required")):
            self.page.login()
            self.page.jobs.pool.shutdown(wait=True)
            self.app.processEvents()
        self.assertEqual(self.page.fields["password"].text(), "secret-test")
        self.assertIsNone(self.store.selection("library:weknora", "weknora"))
        self.assertTrue(self.page.login_button.isEnabled())

    def test_leave_confirmation_retains_or_clears_input(self):
        self.page.add()
        self.page.fields["password"].setText("unsaved")
        with patch("ui.account_connections.ProductMessageDialog.exec_result", return_value="stay"):
            self.assertFalse(self.page.confirm_leave())
        self.assertEqual(self.page.fields["password"].text(), "unsaved")
        with patch("ui.account_connections.ProductMessageDialog.exec_result", return_value="discard"):
            self.assertTrue(self.page.confirm_leave())
        self.assertEqual(self.page.fields["password"].text(), "")

    def test_invalid_template_keeps_editor_content(self):
        self.page.edit_template()
        self.page.template_editor.setPlainText('{"password":"do-not-distribute"}')
        self.page.save_template()
        self.assertIn("未保存", self.page.notice.text())
        self.assertTrue(self.page.template_editor.toPlainText())

    def test_narrow_layout_scrolls_and_theme_is_scoped(self):
        self.page.add()
        self.page.resize(460, 600)
        self.page.template_tools_button.setChecked(True)
        self.page.edit_template()
        self.app.processEvents()
        scroll = self.page.findChild(QScrollArea)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        old = DesignTokens.bg_app
        try:
            DesignTokens.bg_app = "#223344"
            self.page.refresh_theme()
            self.assertIn("#AccountConnectionsBody", self.page.styleSheet())
            self.assertIn("#223344", self.page.styleSheet())
        finally:
            DesignTokens.bg_app = old
            self.page.refresh_theme()

    def test_login_is_simple_and_permissions_follow_success(self):
        self.page.add()
        self.assertFalse(self.page.permissions.isVisible())
        self.assertFalse(self.page.template_tools.isVisible())
        self.assertFalse(self.page.more_options.isVisible())
        self.assertFalse(self.page.templates.isVisible())
        self.broker.authenticate(self.page.current_id, {})
        self.page.refresh()
        self.assertTrue(self.page.permissions.isVisible())
        self.assertFalse(self.page.form.isVisible())
        self.assertFalse(self.page.scope_options.isVisible())
        self.page.login()
        self.assertTrue(self.page.form.isVisible())

    def test_keyboard_submission_and_optional_scope_are_preserved(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.page.add()
        self.app.processEvents()
        username, password = self.page.fields["username"], self.page.fields["password"]
        username.setFocus()
        QTest.keyClicks(username, "user")
        QTest.keyClick(username, Qt.Key_Tab)
        self.assertIs(self.app.focusWidget(), password)
        QTest.keyClicks(password, "password")
        QTest.keyClick(password, Qt.Key_Return)
        self.page.jobs.pool.shutdown(wait=True)
        self.app.processEvents()
        self.assertEqual(self.store.get(self.page.current_id)["state"], "ready")
        self.page.scope_button.setChecked(True)
        self.page.resources.setText("kb-1")
        self.page.scope_button.setChecked(False)
        captured = []
        with patch.object(self.page, "run", side_effect=lambda work, done: captured.extend(work())):
            self.page.use_connection()
        self.assertEqual(captured, [("library:weknora", "weknora", ["read"], []), ("knowledge-library", "weknora", ["read"], ["kb-1"])])


if __name__ == "__main__":
    unittest.main()

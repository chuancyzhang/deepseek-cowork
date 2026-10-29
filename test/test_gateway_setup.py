"""Inline gateway flows: async isolation, draft saving and no model generation."""
import copy
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from core.config_manager import ConfigManager
from core.llm.gateway_accounts import GatewayAccount
from core.llm.gateway_catalog import catalog_record, new_catalog_model
from ui.gateway_setup import GatewaySetup
from main import ModelChannelManager


class GatewaySetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.host.show()
        self.setup = GatewaySetup(self.host)
        self.setup.show()
        self.addCleanup(self.dispose)

    def dispose(self):
        worker = self.setup.worker
        self.setup.dispose()
        if worker:
            self.assertTrue(worker.wait(3000))
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait(self):
        deadline = time.monotonic() + 4
        while self.setup.worker and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.setup.worker)

    def account(self, handler):
        return GatewayAccount("https://gateway.test", "new_api", transport=httpx.MockTransport(handler))

    def test_login_multiple_keys_partial_failure_retry_and_import(self):
        calls = []
        failing = {"2": True}
        def handler(request):
            path = request.url.path
            calls.append((request.method, path))
            if path == "/api/status":
                data = {"version": "new"}
            elif path == "/api/user/login":
                data = {"access_token": "management-jwt", "user": {"id": 9}}
            elif path == "/api/token/":
                data = {"items": [{"id": 1, "name": "First", "status": 1}, {"id": 2, "name": "Second", "status": 1}], "total": 2}
            elif path.startswith("/api/token/"):
                key_id = path.split("/")[-1]
                if key_id == "2" and failing["2"]:
                    return httpx.Response(503, request=request)
                data = {"key": f"sk-key{key_id}"}
            elif path == "/v1/models":
                data = [{"id": "same-model", "supported_endpoint_types": ["openai-response"],
                         "supports_vision": False}]
            else:
                self.fail(path)
            return httpx.Response(200, json={"success": True, "data": data}, request=request)
        self.setup.url_input.setText("https://gateway.test")
        self.setup.username.setText("user")
        self.setup.password.setText("secret-password")
        self.setup.account = self.account(handler)
        self.setup.connect_account()
        self.wait()
        self.assertEqual(self.setup.password.text(), "")
        for item in self.setup._key_items():
            item.setCheckState(Qt.Checked)
        self.setup.discover()
        self.wait()
        self.assertIn("1", self.setup.results)
        self.assertIn("2", self.setup.errors)
        self.setup.selection._select_all()
        failing["2"] = False
        self.setup.retry_failed()
        self.wait()
        self.assertFalse(self.setup.errors)
        self.assertIn(("1", "same-model"), self.setup.selection.selected_pairs())
        self.setup.selection._select_all()
        received = []
        self.setup.channelsReady.connect(received.append)
        self.setup.finish_import()
        channels = received[0]
        self.assertEqual(len(channels), 2)
        self.assertNotEqual(channels[0]["models"][0]["id"], channels[1]["models"][0]["id"])
        self.assertEqual(channels[0]["gateway_source"]["service_id"], channels[1]["gateway_source"]["service_id"])
        self.assertNotIn("management-jwt", json.dumps(channels))
        self.assertNotIn("secret-password", json.dumps(channels))
        self.assertFalse(any(path in {"/v1/responses", "/v1/chat/completions"} for _, path in calls))

    def test_retired_login_result_cannot_replace_current_flow(self):
        def handler(request):
            time.sleep(.04)
            data = {"version": "new"} if request.url.path.endswith("status") else {"access_token": "jwt", "user": {"id": 1}}
            return httpx.Response(200, json={"success": True, "data": data}, request=request)
        account = self.account(handler)
        self.setup.account = account
        self.setup.username.setText("u")
        self.setup.password.setText("p")
        self.setup.connect_account()
        worker = self.setup.worker
        self.setup.close_session()
        self.assertTrue(worker.wait(3000))
        self.app.processEvents()
        self.assertIs(self.setup.stack.currentWidget(), self.setup.login_page)
        self.assertEqual(self.setup.keys, [])
        self.assertFalse(account.auth_headers)

    def test_unsupported_models_are_visible_but_not_importable(self):
        self.setup.selection.set_records({"k": {"records": [catalog_record({"id": "video", "supported_endpoint_types": ["video"]}),
                                                              catalog_record({"id": "chat"})]}})
        self.setup.selection._select_all()
        self.assertEqual(self.setup.selection.selected_pairs(), {("k", "chat")})
        self.assertIn("暂不支持", self.setup.selection.model_list.item(0).text())

    def test_cancel_manual_discovery_ignores_success_arriving_after_stop(self):
        self.setup.mode.setCurrentIndex(1)
        self.setup.url_input.setText("https://gateway.test/v1")
        self.setup.api_key.setText("sk-key")
        def delayed(*_args):
            time.sleep(.08)
            return [{"id": "late-model"}]
        with patch("ui.gateway_setup.list_available_models", side_effect=delayed):
            self.setup.discover_manual()
            self.setup.stop()
            self.wait()
        self.assertEqual(self.setup.results, {})
        self.assertIn("已取消", self.setup.status.text())
        self.assertEqual(self.setup.api_key.text(), "sk-key")

    def test_dispose_after_native_thread_finishes_closes_transient_session(self):
        account = self.account(lambda request: httpx.Response(200, json={"data": []}, request=request))
        self.setup.account = account
        self.setup._run("keys", lambda: [], lambda value: None)
        worker = self.setup.worker
        self.assertTrue(worker.wait(3000))
        self.setup.close_session()
        self.app.processEvents()
        self.assertTrue(account.client.is_closed)
        self.assertTrue(self.setup.login_page.isEnabled())

    def test_probe_reveals_matching_account_label_without_sending_password(self):
        calls = []
        def handler(request):
            calls.append(request.method)
            return httpx.Response(200, json={"success": True, "data": {"version": "new"}}, request=request)
        account = self.account(handler)
        self.setup.url_input.setText("https://gateway.test")
        with patch("ui.gateway_setup.GatewayAccount", return_value=account):
            self.setup.connect_or_probe()
            self.wait()
        self.assertFalse(self.setup.login_credentials.isHidden())
        self.assertEqual(self.setup.platform.currentData(), "new_api")
        self.assertEqual(calls, ["GET"])

    def test_constructing_in_visible_host_does_not_flash_top_level_widgets(self):
        shown = []
        class Tracker(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and obj.parentWidget() is None:
                    shown.append(type(obj).__name__)
                return False
        tracker = Tracker()
        self.app.installEventFilter(tracker)
        extra = GatewaySetup(self.host)
        extra.show()
        self.app.processEvents()
        self.app.removeEventFilter(tracker)
        extra.dispose()
        self.assertEqual(shown, [])

    def test_native_source_and_metadata_round_trip_and_grouping(self):
        source = {"platform": "new_api", "site_url": "https://gateway.test", "account_id": "1", "service_id": "site-1",
                  "key_id": "1", "key_name": "Key 1", "group_name": "Group", "group_id": "g"}
        channels = []
        for key in ("1", "2"):
            record = catalog_record({"id": "same", "supported_endpoint_types": ["openai-response"], "supports_vision": True})
            channels.append({"channel_id": "c" + key, "display_name": "Gateway", "provider_type": "openai", "base_url": "https://gateway.test/v1",
                             "api_key": "sk-" + key, "gateway_source": dict(source, key_id=key),
                             "models": [new_catalog_model(record, "c" + key, "https://gateway.test/v1")]})
        with tempfile.TemporaryDirectory() as directory, patch("core.config_manager.get_app_data_dir", return_value=directory), patch("core.config_manager.get_base_dir", return_value=directory):
            config = ConfigManager()
            config.set_model_channels(channels, channels[0]["models"][0]["id"])
            restored = ConfigManager().get_model_channels()
            self.assertEqual(restored[0]["gateway_source"], source)
            manager = ModelChannelManager(restored, self.host, channels[0]["models"][0]["id"])
            self.assertEqual(manager.service_list.count(), 1)
            manager.open_service("site-1")
            self.assertEqual(manager.channel_combo.count(), 2)
            model = manager.editors[0]._models()[0]
            model["supports_vision"] = False
            manager.accept_channels(channels)
            self.assertFalse(manager.editors[0]._models()[0]["supports_vision"])
            self.assertEqual(len(manager.editors), 2)
            self.assertEqual(len(manager.editors[0]._models()), 1)
            self.assertEqual(manager.get_default_model_id(), channels[0]["models"][0]["id"])
            manager.editors[0].api_key_input.setText("sk-manually-replaced")
            self.assertEqual(manager.service_list.count(), 2)
            self.assertNotIn("gateway_source", manager.get_channels()[0])
            self.assertEqual(manager.get_channels()[0]["models"][0]["id"], channels[0]["models"][0]["id"])


if __name__ == "__main__":
    unittest.main()

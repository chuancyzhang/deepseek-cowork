"""A duplicate launch must hand its update receipt to the actual UI owner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from core.single_instance import UiSingleInstanceServer, build_ui_server_name, notify_existing_ui
from core.process_utils import subprocess_kwargs_no_window
from ui.update_lifecycle import UpdateLifecycleMixin


class UpdateStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.install = self.root / "install"
        self.install.mkdir()
        self.identity = "c" * 32
        self.transaction = self.root / "updates/transactions" / self.identity
        self.transaction.mkdir(parents=True)
        self.receipt = self.transaction / "opened.json"
        self.config = {"id": self.identity, "install_dir": str(self.install), "version": "5.3.3"}
        (self.transaction / "config.json").write_text(json.dumps(self.config))
        (self.transaction / "commit.json").write_text(json.dumps({"id": self.identity, "saved": True}))
        (self.transaction / "transaction.json").write_text(json.dumps({"id": self.identity, "phase": "installed"}))
        self.request = {"id": self.identity, "receipt": str(self.receipt), "install_dir": str(self.install)}
        self.host = SimpleNamespace(update_coordinator=SimpleNamespace(root=str(self.root / "updates"), install_dir=str(self.install)))
        self.enterContext(patch.dict(os.environ, {"COWORK_UPDATE_ID": "", "COWORK_UPDATE_RECEIPT": ""}))

    def acknowledge(self, request):
        return UpdateLifecycleMixin.acknowledge_update_startup(self.host, request)

    def test_direct_launch_records_actual_ready_process_and_version(self):
        os.environ.update(COWORK_UPDATE_ID=self.identity, COWORK_UPDATE_RECEIPT=str(self.receipt))
        UpdateLifecycleMixin.acknowledge_update_startup(self.host)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["id"], self.identity)
        self.assertEqual(receipt["pid"], os.getpid())
        self.assertEqual(receipt["install_dir"], str(self.install))
        self.assertNotIn("COWORK_UPDATE_ID", os.environ)

    def test_forwarded_receipt_cannot_cross_installation_transaction_or_path(self):
        requests = [
            {**self.request, "install_dir": str(self.root / "other")},
            {**self.request, "receipt": str(self.root / "outside.json")},
            {**self.request, "id": "../outside"},
        ]
        for request in requests:
            self.assertFalse(self.acknowledge(request))
            self.assertFalse(self.receipt.exists())
        (self.transaction / "transaction.json").write_text(json.dumps({"id": self.identity, "phase": "writing"}))
        self.assertFalse(self.acknowledge(self.request))
        self.assertFalse(self.receipt.exists())

    def test_second_process_hands_off_before_or_after_owner_finishes_loading(self):
        for ready in (False, True):
            with self.subTest(owner_ready=ready):
                self.receipt.unlink(missing_ok=True)
                name = build_ui_server_name(str(self.root / str(ready)))
                server = UiSingleInstanceServer(name, lambda: None)
                try:
                    self.assertTrue(server.start())
                    if ready:
                        server.set_update_startup_handler(self.acknowledge)
                    code = ("import json,sys; from core.single_instance import notify_existing_ui; "
                            "sys.exit(0 if notify_existing_ui(sys.argv[1], update_request=json.loads(sys.argv[2]), timeout_ms=3000) else 1)")
                    child = subprocess.Popen([sys.executable, "-c", code, name, json.dumps(self.request)],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **subprocess_kwargs_no_window())
                    try:
                        deadline = time.monotonic() + 5
                        while child.poll() is None and time.monotonic() < deadline:
                            self.app.processEvents()
                            time.sleep(.01)
                        output, error = child.communicate(timeout=2)
                        self.assertEqual(child.returncode, 0, (output, error))
                        if not ready:
                            self.assertFalse(self.receipt.exists(), "Loading owner must not acknowledge startup yet")
                            server.set_update_startup_handler(self.acknowledge)
                        self.assertEqual(json.loads(self.receipt.read_text())["pid"], os.getpid())
                    finally:
                        if child.poll() is None:
                            child.kill(); child.wait(timeout=5)
                finally:
                    server.stop()


if __name__ == "__main__":
    unittest.main()

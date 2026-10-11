import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.workbench import WorkbenchStore, WorkbenchError, WorkbenchBridge, WorkbenchRequest
from core.execution_authorization import ExecutionAuthorization, trusted_kind
from core.document_preview import cache_pdf
from core.deliverable_editing import create_edit_session, serialize_editor_payload, atomic_save_session
from core.file_write_locks import file_write_lock
from core.workbench_transport import dispatch, handle_host_request


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchStateTest(unittest.TestCase):
    def test_tabs_are_runtime_only_and_conversation_owned(self):
        store = WorkbenchStore()
        first = store.open("a", "file", path=str(ROOT / "main.py"), activate=True)
        self.assertIs(store.open("a", "file", path=str(ROOT / "main.py")), first)
        web = store.open("a", "web", url="https://example.test")
        other = store.open("b", "terminal")
        self.assertEqual(store.active["a"], first.id)
        self.assertEqual(store.active["b"], other.id)
        with self.assertRaises(WorkbenchError):
            store.get("b", web.id)
        store.close("a", first.id)
        self.assertEqual(store.active["a"], web.id)
        self.assertEqual(WorkbenchStore().list("a"), [])

    def test_takeover_revokes_old_revision_and_does_not_replay(self):
        store = WorkbenchStore(); tab = store.open("a", "web")
        version = store.set_owner("a", tab.id, "agent")
        store.set_owner("a", tab.id, "human")
        with self.assertRaises(WorkbenchError):
            store.check_control("a", tab.id, version)
        store.set_owner("a", tab.id, "available")
        with self.assertRaises(WorkbenchError):
            store.check_control("a", tab.id, version)
        self.assertEqual(tab.page_revision, 2)

    def test_close_excludes_own_request_but_cancels_queued_input(self):
        bridge = WorkbenchBridge()
        close = WorkbenchRequest("a", "r", "close", {"tab_id": "t"}, {}, time.monotonic() + 5)
        queued = WorkbenchRequest("a", "r", "terminal_input", {"tab_id": "t"}, {}, time.monotonic() + 5)
        bridge.pending = {close.id: close, queued.id: queued}
        bridge.cancel_tab("a", "t", exclude_request=close)
        self.assertTrue(queued.cancelled)
        self.assertFalse(close.cancelled)
        self.assertFalse(close.event.is_set())

    def test_trusted_context_required_without_desktop(self):
        result = WorkbenchBridge().call("list", {"session_id": "spoofed"}, {})
        self.assertEqual(result["status"], "unavailable")
        auth = ExecutionAuthorization("a", "r", True, "")
        result = WorkbenchBridge().call("list", {}, {"execution_authorization": auth})
        self.assertEqual(result["status"], "unavailable")

    def test_terminal_buffer_is_bounded_and_reports_lost_prefix(self):
        from core.workbench_terminal import TerminalSession
        terminal = TerminalSession("a", "t", {}, str(ROOT))
        terminal.MAX_BUFFER = 100
        terminal._append("a" * 110)
        terminal._append("中文")
        data = terminal.read(0, 1000)
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["output"]), 100)
        self.assertTrue(data["output"].endswith("中文"))
        self.assertEqual(terminal.read(data["cursor"])["output"], "")

    def test_tools_are_deferred_and_readers_have_trusted_provenance(self):
        from core.skill_manager import SkillManager
        from core.tool_registry import TOOL_SOURCE_OPTIONAL
        manager = SkillManager(auto_load=False, load_mcp_tools=False)
        before = [item["function"]["name"] for item in manager.tool_definitions]
        manager.skill_source_kinds["workbench"] = TOOL_SOURCE_OPTIONAL
        manager._load_legacy_implementation("workbench", str(ROOT / "ai_skills/workbench/impl.py"))
        self.assertNotIn("workbench", manager.skill_load_errors)
        names = manager.skill_to_tools["workbench"]
        self.assertEqual(len(names), 10)
        for name in names:
            self.assertTrue(manager.tool_records[name]["should_defer"])
            self.assertFalse(manager.tool_records[name]["always_load"])
        for name in ("browser_tabs", "browser_observe", "terminal_read"):
            self.assertEqual(trusted_kind(name, manager.tools[name]), "read")
        self.assertEqual(trusted_kind("terminal_input", manager.tools["terminal_input"]), "opaque")
        after = [item["function"]["name"] for item in manager.tool_definitions]
        self.assertEqual([name for name in after if name not in names], before)


class WorkbenchTransportTest(unittest.TestCase):
    def test_claim_is_bound_to_run_and_one_shot_after_reconnect(self):
        auth = ExecutionAuthorization("a", "r", True, "")
        sent, results = [], []
        thread = threading.Thread(target=lambda: results.append(dispatch(sent.append, "terminal_input", {"text": "abc"}, {"execution_authorization": auth}, 3)))
        thread.start()
        end = time.monotonic() + 2
        while not sent and time.monotonic() < end: time.sleep(.01)
        rid = sent[0]["data"]["request_id"]
        envelope = {"request_id": rid, "session_id": "a", "run_id": "r"}
        self.assertEqual(handle_host_request(dict(envelope, action="workbench_claim", session_id="b"))["status"], "unavailable")
        claim = handle_host_request(dict(envelope, action="workbench_claim"))
        self.assertEqual(claim["status"], "ok")
        self.assertEqual(handle_host_request(dict(envelope, action="workbench_claim"))["status"], "already_claimed")
        self.assertEqual(handle_host_request(dict(envelope, action="workbench_result", token="wrong"))["status"], "unavailable")
        handle_host_request(dict(envelope, action="workbench_result", token=claim["token"], result={"ok": True}))
        thread.join(2)
        self.assertEqual(results, [{"ok": True}])
        self.assertEqual(handle_host_request(dict(envelope, action="workbench_claim"))["status"], "unavailable")

    def test_stop_revokes_pending_host_execution(self):
        auth = ExecutionAuthorization("a", "r", True, "")
        sent, results = [], []
        thread = threading.Thread(target=lambda: results.append(dispatch(sent.append, "open", {}, {"execution_authorization": auth}, 3)))
        thread.start()
        end = time.monotonic() + 2
        while not sent and time.monotonic() < end: time.sleep(.01)
        auth.cancel(); thread.join(2)
        self.assertFalse(results[0]["ok"])
        self.assertFalse(results[0]["executed"])


class WorkbenchFileTest(unittest.TestCase):
    def test_html_source_preserves_head_script_encoding_and_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "page.html"
            source.write_bytes(b'\xef\xbb\xbf<!doctype html>\r\n<html><head><title>Original</title><script>let x=1</script></head><body>Before</body></html>')
            session, _payload = create_edit_session(str(source), editor_mode="source")
            self.assertEqual(session.descriptor.kind, "text")
            data = serialize_editor_payload(session, source.read_text(encoding="utf-8-sig").replace("Before", "After"))
            result = atomic_save_session(session, data, backup_root=str(Path(directory) / "backups"))
            written = source.read_bytes()
            self.assertTrue(written.startswith(b'\xef\xbb\xbf'))
            self.assertIn(b'\r\n', written)
            self.assertIn(b'<script>let x=1</script>', written)
            self.assertIn(b'<title>Original</title>', written)
            self.assertIn(b'After', written)
            self.assertTrue(Path(result.backup_path).exists())

    def test_pdf_cache_survives_source_replacement_and_changes_version(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test.pdf"
            source.write_bytes(b'%PDF-1.4\nfirst')
            first = cache_pdf(source, Path(directory) / "cache")
            source.write_bytes(b'%PDF-1.4\nsecond')
            second = cache_pdf(source, Path(directory) / "cache")
            source.unlink()
            self.assertNotEqual(first, second)
            self.assertEqual(Path(first).read_bytes(), b'%PDF-1.4\nfirst')

    def test_managed_write_waits_for_same_path_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "file.txt")
            started, acquired = threading.Event(), threading.Event()
            def writer():
                started.set()
                with file_write_lock(path): acquired.set()
            with file_write_lock(path):
                thread = threading.Thread(target=writer); thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(acquired.wait(.1))
            thread.join(2)
            self.assertTrue(acquired.is_set())


class WorkbenchFileUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from main import MainWindow
        self.window = MainWindow()

    def tearDown(self):
        self.window._release_deliverable_edit_session(show_preview=False)
        self.window.close(); self.window.deleteLater(); self.app.processEvents()

    def wait(self, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate(): return
            time.sleep(.01)
        self.fail("UI did not reach expected state")

    def test_external_change_preserves_draft_and_exposes_conflict_actions(self):
        from ui.file_refresh import refresh_file
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notes.txt"; source.write_text("first", encoding="utf-8")
            self.window.select_deliverable(str(source), render_html=True)
            self.window.begin_deliverable_edit()
            self.wait(lambda: self.window.deliverable_edit_state == "ready")
            self.window.deliverable_text_editor.setPlainText("user draft")
            source.write_text("agent version", encoding="utf-8")
            refresh_file(self.window)
            self.assertEqual(self.window.deliverable_text_editor.toPlainText(), "user draft")
            self.assertEqual(source.read_text(), "agent version")
            self.assertEqual(self.window.deliverable_edit_state, "conflict")
            self.assertFalse(self.window.deliverable_conflict_copy.isHidden())
            self.assertFalse(self.window.deliverable_edit_save_btn.isEnabled())

    def test_clean_preview_refreshes_after_atomic_replacement(self):
        from ui.file_refresh import refresh_file
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notes.txt"; source.write_text("first", encoding="utf-8")
            self.window.select_deliverable(str(source), render_html=True)
            replacement = source.with_suffix(".tmp"); replacement.write_text("updated", encoding="utf-8")
            os.replace(replacement, source)
            refresh_file(self.window)
            self.assertEqual(self.window.preview_text.toPlainText(), "updated")
            self.assertEqual(self.window.browser_workbench.active_tab().path, str(source))

    def test_daemon_request_keeps_original_chat_without_stealing_focus(self):
        from core.workbench_transport import run_desktop_request
        class Client:
            def _request(self, data, timeout=None):
                return handle_host_request(data)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "result.txt"; source.write_text("result", encoding="utf-8")
            first = self.window.current_session_id
            second = self.window.create_new_session(workspace_dir=directory)
            self.window.set_current_session(second)
            auth = ExecutionAuthorization(first, "daemon-run", True, "")
            results = []
            hosts = []
            def send(message):
                host = threading.Thread(target=run_desktop_request,
                    args=(Client(), message["data"]["request_id"], first, "daemon-run"))
                hosts.append(host); host.start()
            caller = threading.Thread(target=lambda: results.append(dispatch(send, "open", {"kind": "file", "path": str(source)}, {"execution_authorization": auth}, 5)))
            caller.start()
            self.wait(lambda: bool(results))
            caller.join(1)
            for host in hosts: host.join(1)
            self.assertTrue(results[0]["ok"], results)
            self.assertEqual(self.window.current_session_id, second)
            self.assertEqual(self.window.browser_workbench.store.get(first, results[0]["tab"]["id"]).path, str(source))
            self.assertFalse(any(tab.kind == "file" for tab in self.window.browser_workbench.store.list(second)))


if __name__ == "__main__":
    unittest.main()

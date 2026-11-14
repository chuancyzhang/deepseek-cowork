"""Exercise the desktop RPC against the real worker and a local SSE provider."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
QA_ROOT = Path(os.environ.get("COWORK_QA_ROOT", ROOT / ".tmp" / "desktop" / "tests"))
QA_ROOT.mkdir(parents=True, exist_ok=True)


class Provider(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(value)
        slow = any("SLOW" in str(m.get("content")) for m in value["messages"] if m["role"] == "user")
        guidance_check = any("GUIDANCE-CHECK" in str(m.get("content")) for m in value["messages"] if m["role"] == "user")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for text in (["阶段输出。"] * 12 if guidance_check else ["已收到。", "这是本地流式验证。"] if not slow else ["保留这部分回答。"] + ["继续输出。"] * 200):
                chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture",
                         "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
                time.sleep(.05 if not slow else .12)
            self.wfile.write(b'data: {"id":"fixture","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            self.wfile.flush()
        except (ConnectionError, OSError):
            pass


class ServiceClient:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lines = queue.Queue()
        self.events = []
        self.log = (self.directory / "service.log").open("a", encoding="utf-8")
        frozen = os.environ.get("COWORK_TEST_DESKTOP_SERVICE")
        # Reject even lazy Qt imports, regardless of what the developer's
        # interpreter happens to have installed. Exercise the real service.
        bootstrap = '''import importlib.abc, runpy, sys
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6', 'PyQt5', 'PyQt6', 'shiboken6', 'qtawesome', 'qdarktheme'}:
            raise ImportError('Desktop service must not import Qt: ' + fullname)
sys.meta_path.insert(0, NoQt())
import os, faulthandler
if os.environ.get('COWORK_TEST_TRACE_TIMEOUT'):
    faulthandler.dump_traceback_later(12)
runpy.run_module('core.desktop_service', run_name='__main__')
'''
        command = [frozen] if frozen else [sys.executable, "-X", "utf8", "-B", "-c", bootstrap]
        self.proc = subprocess.Popen([*command, "--data-dir", str(self.directory)],
                                     cwd=self.directory if frozen else ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                     text=True, encoding="utf-8", env={**os.environ, "TEMP": str(QA_ROOT), "TMP": str(QA_ROOT), "NO_PROXY": "127.0.0.1,localhost,::1"},
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.until(lambda v: v.get("event") == "ready", 40)
        except BaseException:
            self.close(kill=True)
            raise

    def read(self):
        for line in self.proc.stdout:
            self.lines.put(json.loads(line))

    def until(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.lines.get(timeout=max(.01, deadline - time.monotonic()))
            if value.get("event"):
                self.events.append(value)
            if predicate(value):
                return value
        raise TimeoutError("Service event not received; inspect isolated service.log")

    def call(self, method, **params):
        identity = uuid.uuid4().hex
        self.proc.stdin.write(json.dumps({"id": identity, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        response = self.until(lambda v: v.get("id") == identity)
        if "error" in response:
            raise RuntimeError(response["error"]["message"])
        return response["result"]

    def wait_run(self, cid, status=None):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            value = self.call("conversation.get", conversation_id=cid)
            if value["run"] and (value["run"]["status"] == status if status else value["run"]["status"] not in {"queued", "running", "stopping"}):
                return value
            time.sleep(.05)
        raise TimeoutError("Run did not reach expected status")

    def close(self, kill=False):
        try:
            if self.proc.poll() is None:
                if kill:
                    self.proc.kill()
                else:
                    self.call("app.shutdown")
                    self.proc.stdin.close()
                self.proc.wait(timeout=25)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.log.close()


class DesktopShutdownTests(unittest.TestCase):
    def test_shutdown_completion_waits_for_saving_and_failure_keeps_retry_available(self):
        from core.desktop_service import DesktopService
        from types import SimpleNamespace
        from unittest.mock import Mock
        service = DesktopService.__new__(DesktopService)
        service.lock = threading.RLock()
        service.shutdown_lock = threading.Lock()
        service.shutdown_complete = service.closing = False
        service.streams = {}
        service.store = SimpleNamespace(active_runs=lambda: [])
        service.state = SimpleNamespace(update_preparing=False, prepare_update_shutdown=lambda: {"saved": True})
        for name in ("updates", "scheduler", "messaging", "workbench", "jobs", "models", "ledger"):
            setattr(service, name, Mock())
        entered, release = threading.Event(), threading.Event()
        def fail_save():
            entered.set()
            self.assertTrue(release.wait(5))
            raise OSError("injected save failure")
        service.ledger.close.side_effect = fail_save
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.shutdown)
            self.assertTrue(entered.wait(5))
            self.assertFalse(service.shutdown_complete)
            release.set()
            with self.assertRaisesRegex(OSError, "save failure"):
                future.result(timeout=5)
        self.assertFalse(service.closing)
        self.assertFalse(service.state.update_preparing)
        self.assertFalse(service.shutdown_complete)
        service.ledger.close.side_effect = None
        self.assertEqual(service.shutdown(), {"stopped": True})
        self.assertTrue(service.shutdown_complete)
        self.assertEqual(service.shutdown(), {"stopped": True})
        self.assertEqual(service.ledger.close.call_count, 2)


class DesktopStartupTests(unittest.TestCase):
    def test_long_legacy_paths_are_reused_without_copy_or_import(self):
        from core.chat_storage import ChatStorage
        from core.platform_runtime import platform_runtime
        from core.runtime_journal import RuntimeJournal
        import sqlite3
        from contextlib import closing
        import shutil
        import stat
        directory = tempfile.mkdtemp(dir=QA_ROOT)
        self.assertTrue(Path(directory).resolve().is_relative_to(QA_ROOT.resolve()))
        def remove_readonly(function, path, error):
            if not isinstance(error, PermissionError):
                raise error
            os.chmod(path, stat.S_IWRITE)
            function(path)
        self.addCleanup(shutil.rmtree, platform_runtime.filesystem_path(directory), onexc=remove_readonly)
        root = Path(directory) / ("production-profile-" + "x" * 45)
        history = root / "chat_history"
        history.mkdir(parents=True)
        original = ChatStorage(str(history / "chat_history.sqlite"))
        original.upsert_conversation("existing", title="原历史仍在")
        relative = Path("runtime_journal_v2/sessions") / ("a" * 64) / "attempts" / (("b" * 64) + ".json")
        encoded = json.dumps(RuntimeJournal._envelope({"status": "completed", "text": "原运行记录"}), ensure_ascii=False).encode("utf-8")
        source = platform_runtime.filesystem_path(history / relative)
        source.parent.mkdir(parents=True)
        source.write_bytes(encoded)
        os.chmod(source, stat.S_IREAD)
        self.assertGreater(len(str(history / relative)), 260)
        for _ in range(2):
            client = ServiceClient(root)
            try:
                self.assertEqual(Path(client.call("settings.get")["data_dir"]), root)
                self.assertEqual(client.call("conversation.get", conversation_id="existing")["title"], "原历史仍在")
                self.assertFalse((root / "desktop-v2").exists())
                self.assertFalse((root / "updates/data-backups").exists())
                with closing(sqlite3.connect(history / "chat_history.sqlite")) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM runtime_records").fetchone()[0], 0)
                self.assertEqual(source.read_bytes(), encoded)
                self.assertFalse(source.stat().st_mode & stat.S_IWUSR)
            finally:
                client.close()
        self.assertEqual(ChatStorage(str(history / "chat_history.sqlite")).get_conversation_record("existing")["title"], "原历史仍在")


class DesktopIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=QA_ROOT)
        self.client = ServiceClient(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: self.client.close())
        settings = self.client.call("settings.save_model", name="Local fixture", model_name="fixture", api_key="test-only",
                                    base_url=f"http://127.0.0.1:{self.server.server_address[1]}/v1")
        self.model_id = settings["selected_model_id"]

    def submit(self, cid, text="普通验证"):
        params = dict(conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex, text=text, model_id=self.model_id)
        self.client.call("conversation.send", **params)
        return params

    def test_retired_ppt_chat_preserves_history_and_sends_without_special_workflow(self):
        from core.chat_storage import ChatStorage
        from core.desktop_store import DesktopStore

        conversation = self.client.call("conversation.create")
        cid = conversation["id"]
        data = Path(self.client.call("settings.get")["data_dir"])
        storage = ChatStorage(str(data / "chat_history" / "chat_history.sqlite"))
        template = Path(conversation["meta"]["workspace_dir"]) / "original.pptx"
        template.write_bytes(b"original-file-kept")
        legacy = {"ppt_agent_mode": True, "ppt_agent_strategy": "frontend_slides",
                  "ppt_agent_template_file": str(template), "ppt_agent_output_format": "pptx",
                  "workflow_mode": "office_html_first", "office_output_profile": "ppt"}
        storage.update_conversation_meta(cid, legacy)
        storage.replace_messages(cid, [
            {"id": "old-user", "role": "user", "content": "原演示需求"},
            {"id": "old-answer", "role": "assistant", "content": "已有演示成果"},
        ])
        before = storage.get_messages(cid)
        loaded = self.client.call("conversation.get", conversation_id=cid)
        self.assertTrue(loaded["meta"]["ppt_agent_mode"])
        self.assertEqual(loaded["messages"], before)

        params = self.submit(cid, text="继续整理我的演示资料")
        finished = self.client.wait_run(cid)
        self.assertEqual(finished["run"]["status"], "completed")
        payload = json.loads(DesktopStore(storage).get_run(params["request_id"])["payload"])
        context = payload["runtime_context"]
        self.assertFalse(any(key.startswith("ppt_agent_") for key in context))
        self.assertEqual(context["workflow_mode"], "")
        self.assertEqual(storage.get_messages(cid)[:len(before)], before)
        self.assertEqual(template.read_bytes(), b"original-file-kept")
        for key, value in legacy.items():
            self.assertEqual(storage.get_conversation_meta(cid)[key], value)

    def test_message_context_snapshot_is_stable_and_absent_from_provider_projection(self):
        from core.agent import _clean_reasoning_content_by_turn
        cid = self.client.call("conversation.create")["id"]
        source = self.client.call("conversation.create")["id"]
        self.submit(source)
        self.client.wait_run(source)
        reference = self.client.call("conversation.reference", conversation_id=cid, source_id=source)
        preferences = {"selected_skill_names": ["document-reader"], "conversation_refs": [reference],
                       "knowledge_refs": [{"source": "weknora", "title": "发送时的资料"}]}
        params = dict(conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                      text="记录本轮选择", model_id=self.model_id, preferences=preferences)
        self.client.call("conversation.send", **params)
        self.client.wait_run(cid)
        original = self.client.call("conversation.get", conversation_id=cid)["messages"][0]
        self.assertEqual(original["meta"]["ui_context"], {"skills": ["document-reader"], "knowledge": [{"title": "发送时的资料"}]})
        self.assertEqual(original["meta"]["conversation_refs"][0]["id"], reference["id"])
        self.client.call("conversation.preferences", conversation_id=cid, value={"selected_skill_names": [], "knowledge_refs": []})
        reopened = self.client.call("conversation.get", conversation_id=cid)["messages"][0]
        self.assertEqual(reopened["meta"]["ui_context"], original["meta"]["ui_context"])
        without = {**original, "meta": {key: value for key, value in original["meta"].items() if key != "ui_context"}}
        self.assertEqual(_clean_reasoning_content_by_turn([original], drop_meta=True),
                         _clean_reasoning_content_by_turn([without], drop_meta=True))
        request = next(value for value in reversed(Provider.requests)
                       if any("记录本轮选择" in str(message.get("content")) for message in value["messages"]))
        self.assertNotIn("ui_context", json.dumps(request))
        self.assertTrue(all("meta" not in message for message in request["messages"]))

    def test_durable_send_deduplication_and_isolation(self):
        first = self.client.call("conversation.create")
        second = self.client.call("conversation.create")
        params = self.submit(first["id"])
        self.client.call("conversation.send", **params)
        result = self.client.wait_run(first["id"])
        self.assertEqual(result["id"], first["id"])
        self.assertEqual(result["title"], "普通验证")
        self.assertEqual(result["meta"]["workspace_dir"], first["meta"]["workspace_dir"])
        self.assertEqual(result["run"]["status"], "completed", result["run"])
        self.assertEqual(sum(m["id"] == params["message_id"] for m in result["messages"]), 1)
        self.assertTrue(any("本地流式验证" in str(m.get("content")) for m in result["messages"]))
        self.assertEqual(self.client.call("conversation.get", conversation_id=second["id"])["messages"], [])
        with self.assertRaises(RuntimeError):
            self.client.call("conversation.send", **{**params, "text": "不同内容"})
        self.client.close()
        self.client = ServiceClient(self.temp.name)
        self.assertEqual(self.client.call("conversation.get", conversation_id=first["id"])["messages"], result["messages"])

    def test_stop_guidance_and_crash_recovery(self):
        cid = self.client.call("conversation.create")["id"]
        params = self.submit(cid, "SLOW")
        self.client.until(lambda v: v.get("event") == "runtime" and v.get("data", {}).get("type") == "content")
        message = {"id": uuid.uuid4().hex, "role": "user", "content": "补充内容", "meta": {"kind": "user_guidance", "turn_id": params["request_id"]}}
        answer = self.client.call("conversation.steer", conversation_id=cid, run_id=params["request_id"], message=message)
        self.assertTrue(answer["message_committed"])
        self.client.call("conversation.stop", conversation_id=cid, run_id=params["request_id"])
        value = self.client.wait_run(cid)
        self.assertEqual(value["run"]["status"], "interrupted")
        self.assertTrue(any(m["id"] == message["id"] for m in value["messages"]))
        self.assertTrue(any(e.get("payload", {}).get("type") == "content" for e in value["events"]))
        self.submit(cid, "SLOW AGAIN")
        self.client.until(lambda v: v.get("event") == "runtime" and v.get("data", {}).get("type") == "content")
        self.client.close(kill=True)
        self.client = ServiceClient(self.temp.name)
        recovered = self.client.call("conversation.get", conversation_id=cid)
        self.assertEqual(recovered["run"]["status"], "interrupted")
        self.assertTrue(any(e.get("payload", {}).get("delta", "").startswith("保留") for e in recovered["events"]))

    def test_guidance_finishes_without_conflicting_with_its_durable_receipt(self):
        cid = self.client.call("conversation.create")["id"]
        params = self.submit(cid, "GUIDANCE-CHECK")
        self.client.until(lambda v: v.get("event") == "runtime" and v.get("data", {}).get("type") == "content")
        message = {"id": uuid.uuid4().hex, "role": "user", "content": "按补充要求继续",
                   "meta": {"kind": "user_guidance", "turn_id": params["request_id"]}}
        result = self.client.call("conversation.steer", conversation_id=cid, run_id=params["request_id"], message=message)
        self.assertTrue(result["accepted"] and result["message_committed"])
        value = self.client.wait_run(cid)
        self.assertEqual(value["run"]["status"], "completed", value["run"].get("error"))
        saved = [m for m in value["messages"] if m["id"] == message["id"]]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["content"], message["content"])
        self.assertEqual(saved[0]["meta"]["request_id"], params["request_id"])
        self.client.close()
        self.client = ServiceClient(self.temp.name)
        self.assertEqual(self.client.call("conversation.get", conversation_id=cid)["messages"], value["messages"])

    def test_stop_immediately_after_acceptance(self):
        cid = self.client.call("conversation.create")["id"]
        params = self.submit(cid, "SLOW")
        self.client.call("conversation.stop", conversation_id=cid, run_id=params["request_id"])
        value = self.client.wait_run(cid)
        self.assertEqual(value["run"]["status"], "interrupted")
        self.assertEqual(sum(m["id"] == params["message_id"] for m in value["messages"]), 1)

    def test_context_setting_is_saved_for_next_run(self):
        cid = self.client.call("conversation.create")["id"]
        params = self.submit(cid, "SLOW")
        settings = self.client.call("settings.context", target_tokens=300000)
        self.assertTrue(settings["context_policy"]["enabled"])
        # An uncertain send receipt can be retried without accepting twice,
        # even after the policy changed. The active run retains its snapshot.
        response = self.client.call("conversation.send", **params)
        self.assertEqual(response["run"]["id"], params["request_id"])
        self.client.call("conversation.stop", conversation_id=cid, run_id=params["request_id"])
        with self.assertRaises(RuntimeError):
            self.client.call("settings.context", target_tokens=999999)

    def test_edit_recovery_conflict_and_workspace_boundary(self):
        cid = self.client.call("conversation.create")["id"]
        original = self.client.call("workspace.create", conversation_id=cid, name="文档.md", text="原始内容")
        self.client.call("draft.save", conversation_id=cid, value={"text": "草稿", "attachments": []})
        self.client.call("view.save", conversation_id=cid, value={"editors": [{"path": original["path"], "text": "未保存编辑", "fingerprint": original["fingerprint"]}]})
        Path(original["path"]).write_text("外部修改", encoding="utf-8")
        self.client.close()
        self.client = ServiceClient(self.temp.name)
        value = self.client.call("conversation.get", conversation_id=cid)
        self.assertEqual(value["draft"]["text"], "草稿")
        reopened = self.client.call("file.open", conversation_id=cid, path=original["path"], expected_fingerprint=original["fingerprint"])
        self.assertTrue(reopened["conflict"])
        with self.assertRaises(RuntimeError):
            self.client.call("file.save", conversation_id=cid, id=reopened["id"], payload="未保存编辑")
        self.assertEqual(Path(original["path"]).read_text(encoding="utf-8"), "外部修改")
        with self.assertRaises(RuntimeError):
            self.client.call("file.open", conversation_id=cid, path=str(ROOT / "README.md"))

    def test_history_edit_guard_undo_branch_and_reference(self):
        cid = self.client.call("conversation.create")["id"]
        params = self.submit(cid)
        original = self.client.wait_run(cid)
        prepared = self.client.call("history.prepare", conversation_id=cid, message_id=params["message_id"])
        guard = {key: prepared[key] for key in ("hash", "revision")}
        edited = self.client.call("history.edit", conversation_id=cid, message_id=params["message_id"], guard=guard, text="修改后的输入")
        self.assertEqual(len(edited["conversation"]["messages"]), 1)
        self.assertEqual(edited["conversation"]["messages"][0]["content"], "修改后的输入")
        self.assertEqual(edited["conversation"]["events"], [])
        self.assertIsNone(edited["conversation"]["run"])
        with self.assertRaises(RuntimeError):
            self.client.call("history.delete", conversation_id=cid, message_id=params["message_id"], guard=guard)
        restored = self.client.call("history.undo", conversation_id=cid, undo=edited["undo"])
        self.assertEqual(restored["messages"], original["messages"])
        answer = next(message for message in reversed(original["messages"]) if message["role"] == "assistant")
        branch = self.client.call("conversation.branch", conversation_id=cid, message_id=answer["id"])
        self.assertFalse({message["id"] for message in branch["messages"]} & {message["id"] for message in original["messages"]})
        reference = self.client.call("conversation.reference", conversation_id=branch["id"], source_id=cid)
        self.assertTrue(Path(reference["snapshot_path"]).is_relative_to(Path(branch["meta"]["workspace_dir"])))
        self.assertIn("普通验证", Path(reference["snapshot_path"]).read_text(encoding="utf-8"))

    def test_draft_revisions_and_multiple_models_survive_updates(self):
        cid = self.client.call("conversation.create")["id"]
        saved = self.client.call("draft.save", conversation_id=cid, revision=-1, value={"text": "初稿", "attachments": []})
        self.assertEqual(saved["revision"], 0)
        with self.assertRaises(RuntimeError):
            self.client.call("draft.save", conversation_id=cid, revision=-1, value={"text": "过时草稿", "attachments": []})
        self.assertEqual(self.client.call("conversation.get", conversation_id=cid)["draft"]["text"], "初稿")
        settings = self.client.call("settings.get")
        selected = next(model for model in settings["models"] if model["id"] == self.model_id)
        updated = self.client.call("settings.save_model", channel_id=selected["channel_id"], model_name="fixture-second", base_url=selected["base_url"])
        self.assertTrue(any(model["id"] == self.model_id for model in updated["models"]))
        self.assertTrue(any(model["model_name"] == "fixture-second" for model in updated["models"]))
        params = dict(conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                      model_id=self.model_id, text="发送初稿", draft_revision=saved["revision"])
        receipt = self.client.call("conversation.send", **params)
        self.assertEqual(receipt["draft"]["value"]["text"], "")
        self.assertEqual(receipt["draft"]["revision"], 1)
        self.client.call("draft.save", conversation_id=cid, revision=1, value={"text": "下一条消息", "attachments": []})
        self.client.call("conversation.send", **params)
        self.assertEqual(self.client.call("conversation.get", conversation_id=cid)["draft"]["text"], "下一条消息")
        self.client.wait_run(cid)

    def test_host_forms_restore_encrypted_without_saving_configuration(self):
        before = self.client.call("settings.get")
        value = {"form:model": {"value": {"api_key": "unsaved-test-secret", "name": "待保存"}, "saved": {"name": "原设置"}}}
        receipt = self.client.call("host.recovery.save", value=value, revision=-1)
        self.assertEqual(self.client.call("settings.get"), before)
        with self.assertRaises(RuntimeError):
            self.client.call("host.recovery.save", value={}, revision=-1)
        self.client.close()
        self.client = ServiceClient(self.temp.name)
        restored = self.client.call("host.recovery.get")
        self.assertEqual(restored, {"value": value, "revision": receipt["revision"]})
        import sqlite3
        from contextlib import closing
        database = Path(self.temp.name) / "chat_history/chat_history.sqlite"
        with closing(sqlite3.connect(database)) as db:
            encoded = db.execute("SELECT value FROM desktop_view_state WHERE key='host_forms'").fetchone()[0]
        self.assertNotIn("unsaved-test-secret", encoded)


class DesktopTransactionTests(unittest.TestCase):
    def test_child_controls_keep_session_and_submission_identity(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from core.desktop_store import DesktopChatStorage, DesktopStore
        from core.desktop_conversations import ConversationActions
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            storage = DesktopChatStorage(str(Path(directory) / 'history.db'))
            storage.upsert_conversation('chat', title='当前任务', meta={})
            storage.upsert_conversation('other', title='其他任务', meta={})
            storage.upsert_agent('child', 'chat', name='核对资料', status='running', meta={'execution_owner':'daemon'})
            service = SimpleNamespace(storage=storage, store=DesktopStore(storage), config=Mock(),
                request_agent=Mock(return_value={'result':{'accepted':True,'message_id':'supplement'}}))
            actions = ConversationActions(service)
            request = {'conversation_id':'chat','agent_id':'child','message_id':'supplement','content':'保留来源'}
            result = actions.dispatch('agent.send', request)
            self.assertTrue(result['accepted'])
            self.assertEqual(service.request_agent.call_args.args[0], {
                'action':'subagent_control','session_id':'chat','operation':'send',
                'agent_id':'child','message_id':'supplement','content':'保留来源'})
            with self.assertRaisesRegex(ValueError, '不属于当前会话'):
                actions.dispatch('agent.send', {**request,'conversation_id':'other'})
            self.assertEqual(service.request_agent.call_count, 1)
            service.request_agent.side_effect = ConnectionError('暂时断开')
            with self.assertRaises(ConnectionError):
                actions.dispatch('agent.send', request)
            self.assertEqual(service.request_agent.call_count, 2)
            service.request_agent.side_effect = None
            actions.dispatch('agent.stop', {'conversation_id':'chat','agent_id':'child','run_id':'child-run'})
            self.assertEqual(service.request_agent.call_args.args[0]['run_id'], 'child-run')
            self.assertEqual(service.request_agent.call_args.args[0]['operation'], 'interrupt')

    def test_history_projection_uses_durable_draft_and_preferences_without_mutation(self):
        from core.desktop_store import DesktopChatStorage, DesktopStore
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            storage = DesktopChatStorage(str(Path(directory) / 'history.db'))
            store = DesktopStore(storage)
            for cid in ['empty', 'draft', 'skills', 'cleared', 'file']:
                storage.upsert_conversation(cid, title='新对话', meta={'workspace_dir': directory})
            storage.update_conversation_meta('skills', {'selected_skill_names':['chosen']})
            storage.update_conversation_meta('cleared', {'selected_skill_names':[]})
            store.put_state('view:cleared', {'selectedSkills':['stale']})
            store.put_state('draft:draft', {'text':'保留我的输入', 'attachments':[]})
            store.put_state('view:file', {'editors':[{'text':'未保存', 'saved':''}]})
            before = storage.list_conversation_summaries()
            result = {item['id']: item for item in store.summarize_conversations(before)}
            self.assertFalse(result['empty']['has_content'])
            self.assertFalse(result['cleared']['has_content'])
            self.assertTrue(result['skills']['has_content'])
            self.assertTrue(result['file']['has_content'])
            self.assertEqual(result['draft']['display_title'], '草稿 · 保留我的输入')
            self.assertEqual(storage.list_conversation_summaries(), before)

    def test_task_inspection_returns_call_details_and_keeps_request_snapshot_scoped(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from core.desktop_store import DesktopChatStorage, DesktopStore
        from core.desktop_conversations import ConversationActions
        from core.request_snapshot import RequestSnapshotStore
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            storage = DesktopChatStorage(str(Path(directory) / 'history.db'))
            store = DesktopStore(storage)
            storage.upsert_conversation('chat', title='核对', meta={'selected_skill_names':['selected-now']})
            storage.upsert_conversation('other', title='其他', meta={})
            storage.replace_messages('chat', [
                {'id':'a','role':'assistant','tool_calls':[{'id':'call','function':{'name':'read_file','arguments':'{"path":"a.txt"}'}}]},
                {'id':'t','role':'tool','tool_call_id':'call','content':'读取失败','result_obj':{'error':'文件不存在'},'meta':{'duration':1.2}},
            ])
            service = SimpleNamespace(storage=storage, store=store, config=Mock(),
                state=SimpleNamespace(runtime_journal=Mock()), public_run=lambda run: run, workspace=lambda cid: Path(directory))
            service.config.get_chat_history_dir.return_value = directory
            service.config.get_selected_model_id.return_value = 'model'
            actions = ConversationActions(service)
            snapshot = actions.dispatch('task.inspect', {'conversation_id':'chat'})
            self.assertEqual(snapshot['calls'][0]['arguments'], {'path':'a.txt'})
            self.assertEqual(snapshot['calls'][0]['status'], 'failed')
            self.assertEqual(snapshot['calls'][0]['elapsed_ms'], 1200)
            self.assertEqual(snapshot['current_context']['skills'], ['selected-now'])
            self.assertIsNone(snapshot['submitted_context'])
            requests = RequestSnapshotStore(directory)
            requests.capture('chat','old','previous-request','responses',{'model':'model','input':[]})
            requests.capture('chat','run','request','responses',{'model':'model','input':[{'role':'user','content':'完整内容'}],'api_key':'never-return'})
            full = actions.dispatch('task.context', {'conversation_id':'chat','run_id':'run'})
            self.assertEqual(full['parameters']['input'][0]['content'], '完整内容')
            self.assertNotIn('api_key', full['parameters'])
            with self.assertLogs('desktop.conversations', level='INFO') as logs:
                with self.assertRaisesRegex(ValueError, '后续请求.*返回当前任务'):
                    actions.dispatch('task.context', {'conversation_id':'chat','run_id':'old'})
            self.assertIn('requested_run=old snapshot_run=run reason=run_mismatch', logs.output[0])
            self.assertEqual(actions.dispatch('task.context', {'conversation_id':'chat','run_id':'run'}), full)
            with self.assertRaises(ValueError):
                actions.dispatch('task.context', {'conversation_id':'other'})

    def test_regeneration_rolls_back_history_when_dispatch_cannot_commit(self):
        from core.desktop_store import DesktopChatStorage, DesktopStore
        from core.conversation_integrity import canonical_ledger_messages_hash
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            storage = DesktopChatStorage(str(Path(directory) / "history.db"))
            store = DesktopStore(storage)
            storage.upsert_conversation("chat", title="保留原历史", meta={})
            original = [{"id": "user", "role": "user", "content": "原请求"}, {"id": "answer", "role": "assistant", "content": "原成果"}]
            storage.replace_messages("chat", original)
            original = storage.get_messages("chat")
            with storage._connect() as db:
                db.execute("CREATE TRIGGER fail_dispatch BEFORE INSERT ON desktop_runs BEGIN SELECT RAISE(ABORT, 'simulated disk error'); END")
            with self.assertRaises(Exception):
                store.accept({"conversation_id": "chat", "request_id": "retry", "message_id": "replacement", "content": "新请求", "display_text": "新请求",
                    "rewrite": {"hash": canonical_ledger_messages_hash(original), "revision": 0, "message_id": "user"}})
            self.assertEqual(storage.get_messages("chat"), original)
            self.assertIsNone(store.get_run("retry"))
            self.assertIsNone(store.get_state("history_undo:retry"))

    def test_message_and_run_roll_back_together(self):
        from core.desktop_store import DesktopChatStorage, DesktopStore
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            storage = DesktopChatStorage(str(Path(directory) / "history.db"))
            store = DesktopStore(storage)
            storage.upsert_conversation("chat", title="新对话", meta={})
            with storage._connect() as db:
                db.execute("CREATE TRIGGER fail_dispatch BEFORE INSERT ON desktop_runs BEGIN SELECT RAISE(ABORT, 'simulated disk error'); END")
            with self.assertRaises(Exception):
                store.accept({"conversation_id": "chat", "request_id": "run", "message_id": "message", "content": "不应部分保存", "display_text": "不应部分保存"})
            self.assertEqual(storage.get_messages("chat"), [])
            self.assertIsNone(store.get_run("run"))
            self.assertEqual(storage.get_conversation_record("chat")["title"], "新对话")


if __name__ == "__main__":
    unittest.main()

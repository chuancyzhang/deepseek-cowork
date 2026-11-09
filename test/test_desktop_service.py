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
QA_ROOT = ROOT / ".tmp" / "desktop" / "tests"
QA_ROOT.mkdir(parents=True, exist_ok=True)


class Provider(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(value)
        slow = any("SLOW" in str(m.get("content")) for m in value["messages"] if m["role"] == "user")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for text in (["已收到。", "这是本地流式验证。"] if not slow else ["保留这部分回答。"] + ["继续输出。"] * 200):
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
        command = [frozen] if frozen else [sys.executable, "-X", "utf8", "-B", "-m", "core.desktop_service"]
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


class DesktopTransactionTests(unittest.TestCase):
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

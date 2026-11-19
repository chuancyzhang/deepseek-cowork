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
        messages = value["messages"]
        checkpoint = str(messages[0].get("content", "")).startswith("你负责为同一任务整理有界的交接状态")
        history_seed = any(m["role"] == "user" and m.get("content") == "CONTEXT-LONG-HISTORY" for m in messages)
        followup = any(m["role"] == "user" and m.get("content") == "CONTEXT-FOLLOW-UP" for m in messages)
        slow = any("SLOW" in str(m.get("content")) for m in value["messages"] if m["role"] == "user")
        guidance_check = any("GUIDANCE-CHECK" in str(m.get("content")) for m in value["messages"] if m["role"] == "user")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            if checkpoint or history_seed and not followup:
                text = '{"goal":' if checkpoint else "早期历史。" * 6500
                reason = "length" if checkpoint else "stop"
                for delta, finish in (({"content": text}, None), ({}, reason)):
                    chunk = {"id": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
                return
            trigger = next((m.get("content", "") for m in value["messages"] if m["role"] == "user" and
                            str(m.get("content", "")).startswith("AUTOMATION-TOOL:")), "")
            if trigger and not any(m["role"] == "tool" for m in value["messages"]):
                task_id = trigger.split(":", 1)[1].strip()
                chunk = {"id": "fixture", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
                    "id": "automation-tool-call", "type": "function", "function": {"name": "run_automation",
                    "arguments": json.dumps({"task_id": task_id})}}]}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b'data: {"id":"fixture","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n')
                self.wfile.flush()
                return
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
    def test_mode_enablement_does_not_claim_success_after_save_failure(self):
        from core.config_manager import ConfigManager
        from unittest.mock import Mock
        config = ConfigManager.__new__(ConfigManager)
        config.config = {}
        config._write_config_or_raise = Mock(side_effect=OSError("disk full"))
        with self.assertRaisesRegex(OSError, "disk full"):
            config.set_mode_plugin_enabled("cowork.creation", False)
        self.assertNotIn("disabled_mode_plugins", config.config)
        config.config["disabled_mode_plugins"] = ["cowork.creation"]
        with self.assertRaisesRegex(OSError, "disk full"):
            config.set_mode_plugin_enabled("cowork.creation", True)
        self.assertEqual(config.config["disabled_mode_plugins"], ["cowork.creation"])

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

    def test_sidebar_groups_and_pins_persist_without_changing_workspaces(self):
        client = self.client
        workspace = Path(self.temp.name) / "project"
        workspace.mkdir()
        client.call("project.save", path=str(workspace), pinned=True)
        project_chat = client.call("conversation.create", workspace_dir=str(workspace))
        independent = client.call("conversation.create")
        for chat, name in ((project_chat, "项目聊天"), (independent, "独立聊天")):
            client.call("conversation.rename", conversation_id=chat["id"], title=name)
        project = {"kind": "project", "id": str(workspace)}
        group_id = uuid.uuid4().hex
        created = client.call("sidebar.create_group", group_id=group_id, name="资料整理", entry=project)
        self.assertEqual(client.call("sidebar.create_group", group_id=group_id, name="资料整理", entry=project), created)
        self.assertEqual(next(iter(created["value"]["projects"].values()))["group_id"], group_id)
        with self.assertRaisesRegex(RuntimeError, "分组已不可用"):
            client.call("sidebar.move", entry=project, group_id=uuid.uuid4().hex)
        with self.assertRaisesRegex(RuntimeError, "聊天已不可用"):
            client.call("sidebar.create_group", group_id=uuid.uuid4().hex, name="无有效成员",
                        entry={"kind": "chat", "id": "missing"})
        client.call("sidebar.move", entry={"kind": "chat", "id": project_chat["id"]}, group_id=group_id)
        client.call("conversation.pin", conversation_id=independent["id"], pinned=True)
        unpinned = client.call("sidebar.pin_project", path=str(workspace), pinned=False)
        self.assertEqual(next(iter(unpinned["value"]["projects"].values()))["pinned_at"], 0)
        pinned = client.call("sidebar.pin_project", path=str(workspace), pinned=True)
        self.assertGreater(next(iter(pinned["value"]["projects"].values()))["pinned_at"], 0)
        client.call("project.archive", path=str(workspace))
        with self.assertRaisesRegex(RuntimeError, "请先从归档恢复"):
            client.call("sidebar.pin_project", path=str(workspace), pinned=False)
        client.call("project.restore", path=str(workspace))

        client.close()
        self.client = ServiceClient(self.temp.name)
        page = self.client.call("conversation.list", limit=1, sidebar=True)
        self.assertEqual(page["sidebar"], pinned)
        self.assertEqual(page["sidebar"]["value"]["groups"], [{"id": group_id, "name": "资料整理"}])
        self.assertEqual(len(page["conversations"]), 1)
        summaries = {row["id"]: row for row in page["conversations"] + page["sidebar_conversations"]}
        self.assertEqual(set(summaries), {independent["id"], project_chat["id"]})
        self.assertTrue(summaries[independent["id"]]["meta"]["pinned"])
        self.assertEqual(summaries[independent["id"]]["meta"]["workspace_source"], "chat")
        self.assertEqual(summaries[project_chat["id"]]["meta"]["workspace_dir"], str(workspace.resolve()))
        self.assertEqual(self.client.call("app.bootstrap")["sidebar"], pinned)

    def test_project_save_accepts_a_name_or_an_existing_folder(self):
        root = Path(self.client.call("settings.get")["project_workspace_root"])
        created = self.client.call("project.save", name="运营资料")
        self.assertEqual(Path(created["path"]), root / "运营资料")
        self.assertTrue(Path(created["path"]).is_dir())
        with self.assertRaisesRegex(RuntimeError, "已有同名文件夹"):
            self.client.call("project.save", name="运营资料")
        existing = Path(self.temp.name) / "shared"
        existing.mkdir()
        (existing / "notes.txt").write_text("已有资料", encoding="utf-8")
        associated = self.client.call("project.save", name="客户资料", path=str(existing))
        self.assertEqual(associated["name"], "客户资料")
        self.assertEqual(Path(associated["path"]), existing.resolve())
        self.assertEqual((existing / "notes.txt").read_text(encoding="utf-8"), "已有资料")
        self.assertEqual({project["name"] for project in self.client.call("project.list")}, {"运营资料", "客户资料"})

    def test_project_edit_preserves_chat_workspaces_and_sidebar_placement(self):
        client = self.client
        original, target, occupied = (Path(self.temp.name, folder) for folder in ("project", "replacement", "occupied"))
        for directory in (original, target, occupied):
            directory.mkdir()
        notes = original / "notes.txt"
        notes.write_text("已有资料", encoding="utf-8")
        client.call("project.save", name="原项目", path=str(original))
        client.call("project.save", name="其他项目", path=str(occupied))
        chat = client.call("conversation.create", workspace_dir=str(original))
        client.call("conversation.rename", conversation_id=chat["id"], title="原项目聊天")
        group_id = uuid.uuid4().hex
        client.call("sidebar.create_group", group_id=group_id, name="资料整理", entry={"kind": "project", "id": str(original)})
        placement = client.call("sidebar.pin_project", path=str(original), pinned=True)
        before = client.call("project.list")
        params = {"original_path": str(original), "original_name": "原项目", "name": "编辑后的项目"}
        with self.assertRaisesRegex(RuntimeError, "已关联其他项目"):
            client.call("project.edit", **params, path=str(occupied))
        with self.assertRaisesRegex(RuntimeError, "工作目录不存在"):
            client.call("project.edit", **params, path=str(target / "missing"))
        self.assertEqual(client.call("project.list"), before)
        self.assertEqual(client.call("project.folder", path=str(original))["path"], str(original))
        with self.assertRaisesRegex(RuntimeError, "不在侧栏"):
            client.call("project.folder", path=str(Path(self.temp.name)))

        projects = client.call("project.edit", **params, path=str(target))
        project = next(item for item in projects if item["name"] == "编辑后的项目")
        self.assertEqual(project["path"], str(target))
        self.assertEqual(project["previous_paths"], [str(original)])
        self.assertEqual(client.call("project.edit", **params, path=str(target)), projects)
        self.assertEqual(client.call("project.folder", path=str(target))["path"], str(target))
        with self.assertRaisesRegex(RuntimeError, "项目已变化"):
            client.call("project.edit", **params, path=str(original))
        with self.assertRaisesRegex(RuntimeError, "已关联其他项目"):
            client.call("project.save", name="重复关联", path=str(original))
        self.assertEqual(notes.read_text(encoding="utf-8"), "已有资料")

        client.close()
        self.client = client = ServiceClient(self.temp.name)
        self.assertEqual(client.call("project.list"), projects)
        self.assertEqual(client.call("app.bootstrap")["sidebar"], placement)
        self.assertEqual(client.call("conversation.get", conversation_id=chat["id"])["meta"]["workspace_dir"], str(original))
        self.assertIn(chat["id"], {item["id"] for item in client.call("conversation.search", query="编辑后的项目")})
        layout = client.call("sidebar.pin_project", path=str(target), pinned=False)["value"]
        self.assertEqual(list(layout["projects"].values()), [{"path": str(original), "group_id": group_id, "pinned_at": 0}])
        client.call("project.archive", path=str(target))
        self.assertTrue(next(item for item in client.call("project.list") if item["path"] == str(target))["archived"])
        self.assertEqual(client.call("project.folder", path=str(target))["path"], str(target))

    def test_inferred_project_can_be_renamed_when_its_folder_is_unavailable(self):
        folder = Path(self.temp.name, "inferred")
        self.client.call("conversation.create", workspace_dir=str(folder))
        folder.rmdir()
        self.assertEqual(self.client.call("project.list"), [])
        with self.assertRaisesRegex(RuntimeError, "目录不可用"):
            self.client.call("project.folder", path=str(folder))
        project, = self.client.call("project.edit", original_path=str(folder), original_name="inferred",
                                    name="旧项目资料", path=str(folder))
        self.assertEqual(project["inferred_path"], str(folder))
        self.assertEqual(project["name"], "旧项目资料")
        self.assertFalse(folder.exists())

    def test_automation_uses_real_worker_and_discloses_builtin_guidance_before_first_call(self):
        task = self.client.call("favorites.save", value={"name": "自动分析", "prompt": "AUTOMATION-CHECK 分析销售数据并核对指标口径", "skill_names": ["data-analysis"]})
        result = self.client.call("favorites.run", id=task["id"], request_id="automation-replay")
        self.assertTrue(result["run_id"])
        conversation = self.client.wait_run(result["conversation_id"])
        self.assertEqual(conversation["run"]["status"], "completed")
        replay = self.client.call("favorites.run", id=task["id"], request_id="automation-replay")
        self.assertEqual(replay["run_id"], result["run_id"])
        request = next(value for value in reversed(Provider.requests) if any("AUTOMATION-CHECK" in str(m.get("content")) for m in value["messages"] if m["role"] == "user"))
        system = "\n".join(str(m.get("content")) for m in request["messages"] if m["role"] == "system")
        self.assertIn("本轮可用的内置技能", system)
        self.assertIn("task-automation", system)
        self.assertIn("两个图共用同一计算函数不算独立核验", system)
        self.client.call("favorites.delete", id=task["id"], revision=task["revision"])
        self.assertTrue(any(row["favorite_id"] == task["id"] for row in self.client.call("favorites.history")))
        self.assertEqual(self.client.call("conversation.get", conversation_id=result["conversation_id"])["run"]["status"], "completed")

    def test_builtin_template_guidance_is_loaded_without_manual_skill_selection(self):
        cid = self.client.call("conversation.create")["id"]
        self.submit(cid, "TEMPLATE-CATALOG-CHECK 把参考 PPT 保存为可复用模板 Skill")
        self.assertEqual(self.client.wait_run(cid)["run"]["status"], "completed")
        request = next(value for value in reversed(Provider.requests) if any("TEMPLATE-CATALOG-CHECK" in str(m.get("content")) for m in value["messages"] if m["role"] == "user"))
        system = "\n".join(str(m.get("content")) for m in request["messages"] if m["role"] == "system")
        self.assertIn("用一组与原件不同的新内容试做", system)

    def test_automation_tool_launches_a_distinct_real_worker(self):
        task = self.client.call("favorites.save", value={"name": "工具启动", "prompt": "CHILD-AUTOMATION-RESULT"})
        cid = self.client.call("conversation.create")["id"]
        self.client.call("settings.permission", enabled=True)
        self.submit(cid, "AUTOMATION-TOOL:" + task["id"])
        parent = self.client.wait_run(cid)
        self.assertEqual(parent["run"]["status"], "completed")
        rows = self.client.call("favorites.history")
        row = next(item for item in rows if item["favorite_id"] == task["id"])
        self.assertNotEqual(row["session_id"], cid)
        self.assertTrue(row["run_id"])
        child = self.client.wait_run(row["session_id"])
        self.assertEqual(child["run"]["status"], "completed")
        request = next(value for value in reversed(Provider.requests) if any(m["role"] == "tool" and
                       m.get("tool_call_id") == "automation-tool-call" for m in value["messages"]))
        receipt = next(m["content"] for m in request["messages"] if m["role"] == "tool" and m.get("tool_call_id") == "automation-tool-call")
        self.assertIn(row["run_id"], receipt)

    def test_automation_once_schedule_runs_and_restart_does_not_repeat(self):
        task = self.client.call("favorites.save", value={"name": "单次触发", "prompt": "ONCE-SCHEDULE-RESULT",
            "schedule": {"enabled": True, "schedule_type": "once", "delay_seconds": 1}})
        deadline = time.monotonic() + 18
        rows = []
        while time.monotonic() < deadline:
            rows = self.client.call("favorites.history")
            if rows and rows[0]["status"] == "completed":
                break
            time.sleep(.15)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertFalse(self.client.call("favorites.list")[0]["schedule"]["enabled"])
        self.client.close()
        self.client = ServiceClient(self.temp.name)
        self.assertEqual(self.client.call("favorites.history"), rows)
        self.assertFalse(self.client.call("favorites.list")[0]["schedule"]["enabled"])

    def test_creation_sessions_preserve_source_and_enforce_mode_lifecycle(self):
        from core.chat_storage import ChatStorage
        from core.desktop_store import DesktopStore
        c = self.client
        source = c.call("conversation.create")["id"]
        c.call("draft.save", conversation_id=source, value={"text": "保留原任务", "attachments": []})
        c.call("conversation.preferences", conversation_id=source, value={"mode": "grilling"})
        design = c.call("creation.open", conversation_id=source)
        cid = design["conversation_id"]
        self.assertNotEqual(cid, source)
        self.assertEqual(c.call("creation.open", conversation_id=source), design)
        self.assertEqual(c.call("creation.open", conversation_id=cid), design)
        self.assertEqual(c.call("conversation.get", conversation_id=cid)["meta"]["mode"], "execution")
        original = c.call("conversation.get", conversation_id=source)
        self.assertEqual(original["draft"]["text"], "保留原任务")
        self.assertEqual(original["meta"]["mode"], "grilling")
        with self.assertRaisesRegex(RuntimeError, "退出创造模式"):
            c.call("conversation.preferences", conversation_id=cid, value={"mode": "grilling"})
        params = dict(conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                      text="创建一个阅读主题", model_id=self.model_id, creation_scope="主题")
        c.call("conversation.send", **params)
        c.call("modes.enable", id="cowork.creation", enabled=False)
        finished = c.wait_run(cid)
        self.assertEqual(finished["run"]["status"], "completed")
        self.assertTrue(c.call("conversation.send", **params)["accepted"])
        self.assertEqual(len([m for m in finished["messages"] if m["role"] == "user"]), 1)
        self.assertEqual(finished["messages"][0]["meta"]["display_content"], params["text"])
        with self.assertRaisesRegex(RuntimeError, "已停用"):
            c.call("creation.open", conversation_id=source)
        with self.assertRaisesRegex(RuntimeError, "已停用"):
            self.submit(cid)
        data = Path(c.call("settings.get")["data_dir"])
        storage = ChatStorage(str(data / "chat_history" / "chat_history.sqlite"))
        context = json.loads(DesktopStore(storage).get_run(params["request_id"])["payload"])["runtime_context"]
        self.assertTrue(context["creation_session"])
        self.assertEqual(context["mode"], "execution")
        request = next(value for value in reversed(Provider.requests)
                       if any(params["text"] in str(message.get("content")) for message in value["messages"]))
        self.assertTrue(any("[创造模式" in str(m.get("content")) for m in request["messages"] if m["role"] == "user"))
        self.assertFalse(any("[创造模式" in str(m.get("content")) for m in request["messages"] if m["role"] in {"system", "developer"}))
        c.call("modes.enable", id="cowork.creation", enabled=True)
        c.call("modes.enable", id="cowork.grilling", enabled=False)
        with self.assertRaisesRegex(RuntimeError, "已停用"):
            self.submit(source)
        c.call("conversation.preferences", conversation_id=source, value={"mode": "execution"})
        self.submit(source)
        self.assertEqual(c.wait_run(source)["run"]["status"], "completed")

    def test_creation_theme_candidates_are_isolated_and_survive_reopening(self):
        from core.theme_service import ThemeRepository
        from core.theme_tokens import default_design_tokens
        c = self.client
        ids = [c.call("creation.open", conversation_id=c.call("conversation.create")["id"])["conversation_id"] for _ in range(2)]
        data = c.call("settings.get")["data_dir"]
        previews = [ThemeRepository(data, preview_session=cid).write_preview(name="候选 " + str(i),
                    overrides={"tokens": {"primary": "#2255aa"}}, default_tokens=default_design_tokens(), session_id=cid)
                    for i, cid in enumerate(ids)]
        self.assertIsNone(c.call("themes.preview")["preview"])
        for cid, preview in zip(ids, previews):
            self.assertEqual(c.call("themes.preview", conversation_id=cid)["preview"]["preview_id"], preview["preview_id"])
            self.assertEqual(c.call("creation.open", conversation_id=cid)["conversation_id"], cid)
        with self.assertRaisesRegex(RuntimeError, "主题预览不存在"):
            c.call("themes.commit_preview", conversation_id=ids[1], id=previews[0]["preview_id"], revision=previews[0]["revision"], name="错误候选")
        c.call("themes.commit_preview", conversation_id=ids[0], id=previews[0]["preview_id"], revision=previews[0]["revision"], name="已应用")
        self.assertIsNone(c.call("themes.preview", conversation_id=ids[0])["preview"])
        self.assertEqual(c.call("themes.preview", conversation_id=ids[1])["preview"]["preview_id"], previews[1]["preview_id"])
        self.assertEqual(len([t for t in c.call("themes.list")["themes"] if not t.get("builtin")]), 1)

    def test_builtin_html_expert_selection_reaches_run_and_stays_in_its_conversation(self):
        from core.chat_storage import ChatStorage
        from core.desktop_store import DesktopStore

        c = self.client
        expert = next(item for item in c.call("experts.list") if item["id"] == "builtin-html-creator")
        selection = {"id": expert["id"], "name": expert["name"]}
        cid = c.call("conversation.create")["id"]
        other = c.call("conversation.create")["id"]
        c.call("conversation.preferences", conversation_id=cid, value={"conversation_agent": selection})
        self.assertEqual(c.call("conversation.get", conversation_id=cid)["meta"]["conversation_agent"], selection)
        self.assertFalse(c.call("conversation.get", conversation_id=other)["meta"].get("conversation_agent"))
        params = self.submit(cid, text="核对 HTML 专家角色")
        finished = c.wait_run(cid)
        self.assertEqual(finished["run"]["status"], "completed")
        data = Path(c.call("settings.get")["data_dir"])
        storage = ChatStorage(str(data / "chat_history" / "chat_history.sqlite"))
        context = json.loads(DesktopStore(storage).get_run(params["request_id"])["payload"])["runtime_context"]
        self.assertEqual(context["agent_profile_id"], expert["id"])
        self.assertIn(expert["system_prompt"], context["agent_system_prompt"])
        request = next(value for value in reversed(Provider.requests)
                       if any("核对 HTML 专家角色" in str(message.get("content")) for message in value["messages"]))
        self.assertIn("你是 HTML 创作专家", json.dumps(request["messages"], ensure_ascii=False))
        before = storage.get_messages(cid)
        c.call("conversation.preferences", conversation_id=cid, value={"conversation_agent": {}})
        self.assertEqual(storage.get_messages(cid), before)
        ordinary = self.submit(cid, text="已切回普通助手")
        self.assertEqual(c.wait_run(cid)["run"]["status"], "completed")
        ordinary_context = json.loads(DesktopStore(storage).get_run(ordinary["request_id"])["payload"])["runtime_context"]
        self.assertNotEqual(ordinary_context.get("agent_profile_id"), expert["id"])
        self.assertIn("普通助手", ordinary_context["agent_system_prompt"])

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
        preferences = {"selected_skill_names": ["documents"], "conversation_refs": [reference],
                       "knowledge_refs": [{"source": "weknora", "title": "发送时的资料"}]}
        params = dict(conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                      text="记录本轮选择", model_id=self.model_id, preferences=preferences)
        self.client.call("conversation.send", **params)
        self.client.wait_run(cid)
        original = self.client.call("conversation.get", conversation_id=cid)["messages"][0]
        self.assertEqual(original["meta"]["ui_context"], {"skills": ["documents"], "knowledge": [{"title": "发送时的资料"}]})
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

    def test_edit_and_retry_keep_original_skills_after_composer_selection_changes(self):
        cid = self.client.call("conversation.create")["id"]
        first = dict(conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                     text="常用任务原请求", model_id=self.model_id, skills=["documents"])
        self.client.call("conversation.send", **first)
        self.client.wait_run(cid)
        self.client.call("conversation.preferences", conversation_id=cid, value={"selected_skill_names": []})
        for text in ("修改常用任务请求", "再次修改请求"):
            current = self.client.call("conversation.get", conversation_id=cid)["messages"][0]
            prepared = self.client.call("history.prepare", conversation_id=cid, message_id=current["id"])
            request_id = uuid.uuid4().hex
            self.client.call("conversation.send", conversation_id=cid, request_id=request_id, message_id=uuid.uuid4().hex,
                             text=text, model_id=self.model_id, consume_draft=False,
                             rewrite={"hash": prepared["hash"], "revision": prepared["revision"], "message_id": current["id"]})
            self.client.wait_run(cid)
            rewritten = self.client.call("conversation.get", conversation_id=cid)["messages"][0]
            self.assertEqual(rewritten["meta"]["ui_context"]["skills"], ["documents"])
            inspection = self.client.call("task.inspect", conversation_id=cid, run_id=request_id)
            self.assertEqual(inspection["current_context"]["skills"], [])
            self.assertEqual(inspection["submitted_context"]["skills"], ["documents"])

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
        self.assertEqual(self.client.call("settings.get")["context_policy"], {"enabled": True, "target_tokens": 1000000})
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

    def test_truncated_checkpoint_retries_then_ends_the_run_with_history_and_file(self):
        selected = self.client.call("settings.get")
        model = next(item for item in selected["models"] if item["id"] == self.model_id)
        self.client.call("settings.save_model", channel_id=model["channel_id"], model_id=model["id"],
                         model_name=model["model_name"], base_url=model["base_url"], context_window_tokens=37000)
        self.client.call("settings.context", target_tokens=0)
        cid = self.client.call("conversation.create")["id"]
        self.submit(cid, "CONTEXT-LONG-HISTORY")
        self.assertEqual(self.client.wait_run(cid)["run"]["status"], "completed")
        self.submit(cid, "CONTEXT-FOLLOW-UP")
        self.assertEqual(self.client.wait_run(cid)["run"]["status"], "completed")
        artifact = self.client.call("workspace.create", conversation_id=cid, name="报告.md", text="已完成的报告")
        self.client.call("settings.context", target_tokens=300000)
        before = len(Provider.requests)
        sent = self.submit(cid, "CONTEXT-RESUME")
        failed = self.client.wait_run(cid)
        self.assertEqual(failed["run"]["status"], "failed")
        self.assertIn("输出达到上限", failed["run"]["error"])
        self.assertEqual([request.get("max_tokens") for request in Provider.requests[before:]], [8192, 16384, 16384, 16384])
        events = (event.get("payload", event) for event in failed["events"])
        final = next(event for event in events if event.get("type") == "final" and event.get("run_id") == sent["request_id"])
        self.assertEqual(final["result"]["context_recovery"], "new_chat")
        self.assertEqual(sum(message["id"] == sent["message_id"] for message in failed["messages"]), 1)
        self.assertTrue(any(message.get("content") == "早期历史。" * 6500 for message in failed["messages"]))
        self.assertEqual(Path(artifact["path"]).read_text(encoding="utf-8"), "已完成的报告")

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
        external = self.client.call("file.open", conversation_id=cid, path=str(ROOT / "README.md"))
        self.assertEqual(external["payload"]["kind"], "text")
        with self.assertRaisesRegex(RuntimeError, "工作目录"):
            self.client.call("workspace.create", conversation_id=cid, name=str(ROOT / "forbidden.md"))

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

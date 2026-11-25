"""Provider contracts and durable recovery use a local HTTP fixture, never paid APIs."""
import copy
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import os
import re
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from core.chat_storage import ChatStorage
from core.desktop_jobs import DesktopJobs
from core.desktop_media import DesktopMedia
from core.desktop_store import DesktopStore
from core.multimedia.adapters import Adapter, MediaError
from core.skill_manager import SkillManager
from test_desktop_service import ServiceClient, QA_ROOT

from PIL import Image
_png = io.BytesIO()
Image.new("RGB", (4, 4), "white").save(_png, format="PNG")
PNG = _png.getvalue()


class Config:
    def __init__(self, root, base):
        self.data_dir = str(root)
        self.value = {"enabled_skills": ["multimedia"], "disabled_skills": [], "skill_configs": {}}
        self.channels = [{"channel_id": "media", "display_name": "Fixture", "base_url": base, "api_key": "private-test-key", "models": [], "provider_type": "openai"}]
    def get(self, key, default=None):
        return copy.deepcopy(self.value.get(key, default))
    def set(self, key, value):
        self.value[key] = copy.deepcopy(value)
    def is_skill_enabled(self, name, default_enabled=True):
        return name not in self.value['disabled_skills'] and (name in self.value['enabled_skills'] or default_enabled)
    def set_skill_enabled(self, name, enabled, **_kwargs):
        self.value['enabled_skills'] = [name] if enabled else []
        self.value['disabled_skills'] = [] if enabled else [name]
    def set_skill_config(self, name, values):
        self.value['skill_configs'][name] = copy.deepcopy(values)
    def set_media(self, value):
        self.set_skill_config('multimedia', {'bindings': json.dumps(value['bindings'])})
        self.set_skill_enabled('multimedia', value['enabled'])
    def get_model_channels(self):
        return copy.deepcopy(self.channels)
    def set_model_channels(self, channels, selected=None):
        self.channels = copy.deepcopy(channels)
    def get_selected_model_id(self):
        return ""
    @contextmanager
    def batch_save(self, **_kwargs):
        yield


class MultimediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=QA_ROOT)
        self.root = Path(self.temp.name)
        self.calls, self.fail_download, self.pending = [], False, False
        self.create_responses, self.query_responses, self.download_responses = [], [], []
        self.entered_backoff, self.release_backoff = threading.Event(), threading.Event()
        from core.multimedia.runtime import MediaRuntime
        wait = MediaRuntime.retry_wait
        def fixture_wait(seconds, cancelled, job_cancelled):
            if seconds == 5:
                self.entered_backoff.set()
                while not self.release_backoff.is_set() and not cancelled.is_set() and not job_cancelled.is_set():
                    self.release_backoff.wait(.02)
            wait(.01, cancelled, job_cancelled)
        sleep = patch.object(MediaRuntime, "retry_wait", staticmethod(fixture_wait))
        sleep.start()
        self.addCleanup(sleep.stop)
        test = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def reply(self, value, status=200, mime="application/json"):
                raw = json.dumps(value).encode() if isinstance(value, dict) else value
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-Request-ID", "fixture-request")
                self.end_headers()
                self.wfile.write(raw)
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw) if "application/json" in self.headers.get("Content-Type", "") else raw.decode(errors="replace")
                test.calls.append(("POST", self.path, body, self.headers.get("Authorization")))
                if self.path.endswith("images/generations"):
                    return self.reply({"data": [{"url": test.base + "/output.png"}]})
                if self.path.endswith("/videos"):
                    if test.create_responses:
                        value, status = test.create_responses.pop(0)
                        return self.reply(value, status)
                    return self.reply({"id": "task-1", "video_id": "video-2"})
                if self.path.endswith("/tasks"):
                    return self.reply({"id": "ark-3"})
                if self.path.endswith("/chat/completions"):
                    return self.reply({"choices": [{"message": {"content": "解析结果"}}]})
                if self.path.endswith("/audio/transcriptions"):
                    return self.reply({"text": "转写结果"})
                if self.path.endswith("/audio/speech"):
                    return self.reply(b"ID3fixture", mime="audio/mpeg")
                self.reply({}, 404)
            def do_GET(self):
                test.calls.append(("GET", self.path, None, self.headers.get("Authorization")))
                if self.path.startswith("/agnesapi"):
                    if test.query_responses:
                        value, status = test.query_responses.pop(0)
                        return self.reply(value, status)
                    return self.reply({"status": "in_progress"} if test.pending else {"status": "completed", "url": test.base + "/output.mp4"})
                if "/contents/generations/tasks/" in self.path:
                    return self.reply({"status": "succeeded", "content": {"video_url": test.base + "/output.mp4"}})
                if self.path.startswith("/output"):
                    if test.download_responses:
                        return self.reply({}, test.download_responses.pop(0))
                    return self.reply({"error": {}}, 503) if test.fail_download else self.reply(PNG if self.path.endswith('.png') else b"video", mime="application/octet-stream")
                self.reply({}, 404)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.config = Config(self.root, self.base)
        self.storage = ChatStorage(str(self.root / "history.sqlite"))
        self.events = []
        self.service = SimpleNamespace(config=self.config, storage=self.storage, store=DesktopStore(self.storage), publish=self.events.append,
                                       files=SimpleNamespace(workspace=lambda cid: self.root / cid))
        (self.root / "one").mkdir()
        (self.root / "two").mkdir()
        self.service.jobs = DesktopJobs(self.service)
        self.host = DesktopMedia(self.service)
        self.runtime = self.host.acquire()

    def test_connection_evidence_is_scoped_to_model_protocol_and_credentials(self):
        profile={"channel_id":"chat","base_url":self.base,"model_name":"custom","provider_type":"openai","api_protocol":"chat_completions","api_key":"secret"}
        self.assertIsNone(self.host.model_usage(profile)["chat_supported"])
        self.host.record_connection_test(profile,True,"对话接口测试通过")
        self.assertTrue(self.host.model_usage(profile)["chat_supported"])
        for key,value in (("api_key","changed"),("api_protocol","responses"),("model_name","another"),("base_url",self.base+'/other')):
            self.assertIsNone(self.host.connection_test({**profile,key:value}))
        self.assertNotIn('secret',json.dumps(self.config.value))
        self.host.record_connection_test(profile,False,"当前连接不可用")
        self.assertFalse(self.host.connection_test(profile)["success"])

    def tearDown(self):
        self.service.jobs.close()
        self.host.release()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def profile(self, adapter="agnes", model="fixture"):
        return {"channel_id": "media", "model": model, "adapter": adapter, "base_url": self.base,
                "api_key": "private-test-key", "service_name": "Fixture"}

    def submit(self, kind="image_generate", adapter="agnes", inputs=None, options=None, identity="execution", model="fixture"):
        return self.runtime.submit(kind, "请生成或解析", inputs or [], options or {}, self.profile(adapter, model),
                                   str(self.root / "one"), {"conversation_id": "one", "logical_execution_id": identity})

    def wait(self, identity):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            value = self.runtime.get(identity)
            if identity not in self.runtime.active:
                return value
            time.sleep(.02)
        self.fail("media task did not settle: " + repr(value))

    def test_download_retry_never_resubmits_and_credentials_stay_at_api(self):
        self.fail_download = True
        task = self.submit()
        failed = self.wait(task["id"])
        self.assertEqual(failed["status"], "download_failed")
        self.assertEqual(self.service.jobs.get(failed["job_id"])["status"], "failed")
        self.fail_download = False
        self.config.set_skill_enabled("multimedia", False)
        self.host.dispatch("media.resume", {"id": task["id"], "conversation_id": "one"})
        result = self.wait(task["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(Path(result["files"][0]).read_bytes(), PNG)
        self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 1)
        self.assertTrue(all(item[3] is None for item in self.calls if item[1].startswith('/output')))
        stopped = self.runtime.stop(task["id"], "one")
        self.assertEqual((stopped["status"], stopped["message"]), (result["status"], result["message"]))
        self.assertNotIn("private-test-key", json.dumps(result))
        self.assertNotIn("private-test-key", json.dumps(self.events))
        self.assertEqual(self.submit()["id"], task["id"])
        with self.assertRaisesRegex(ValueError, "已停用"):
            self.submit(identity="new-after-disable")
        with self.assertRaisesRegex(ValueError, "此对话中没有"):
            self.runtime.get(task["id"], "two")

    def test_agnes_video_queries_video_id_and_stop_can_resume_without_resubmit(self):
        self.pending = True
        task = self.submit("video_generate")
        deadline = time.monotonic() + 4
        while not any(item[1].startswith('/agnesapi') for item in self.calls) and time.monotonic() < deadline:
            time.sleep(.02)
        self.runtime.stop(task["id"], "one")
        paused = self.wait(task["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["remote_id"], "video-2")
        self.assertEqual(paused["provider_task_id"], "task-1")
        query = parse_qs(urlsplit(next(item[1] for item in self.calls if item[1].startswith('/agnesapi'))).query)
        self.assertEqual(query, {"video_id": ["video-2"], "model_name": ["fixture"]})
        self.pending = False
        self.runtime.resume(task["id"])
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 1)

    def test_ambiguous_submission_is_durable_and_deduplicated(self):
        for status, payload in ((503, {}), (503, {"error": {"code": "video_queue_full"}, "video_id": "unexpected"}), (408, {})):
            with self.subTest(status=status, payload=payload):
                count = len(self.calls)
                self.create_responses = [(payload, status)]
                task = self.submit("video_generate", identity=f"ambiguous-{count}")
                record = self.wait(task["id"])
                self.assertEqual(record["status"], "unknown")
                self.assertEqual(record["error_detail"]["http_status"], status)
                self.assertFalse(self.runtime.public(record)["can_retry"])
                self.assertFalse(self.runtime.public(record)["can_resume"])
                self.assertEqual(self.submit("video_generate", identity=f"ambiguous-{count}")["id"], task["id"])
                self.runtime.retry(task["id"], "one", record["updated_at"])
                with self.assertRaisesRegex(ValueError, "服务未返回"):
                    self.runtime.resume(task["id"], "one")
                with self.assertRaisesRegex(ValueError, "不接受手填编号"):
                    self.host.dispatch("media.resume", {"id": task["id"], "conversation_id": "one", "remote_id": "1"})
                self.assertEqual(len(self.calls), count + 1)

    def test_queue_rejection_query_and_download_recover_without_user_or_another_generation(self):
        self.release_backoff.set()
        self.create_responses = [({"error": {"code": "video_queue_full", "message": "busy"}}, 503)]
        self.query_responses = [({}, 503)]
        self.download_responses = [503]
        task = self.submit("video_generate", options={"seconds": 5, "size": "720p"}, model="agnes-video-2.5-flash")
        result = self.wait(task["id"])
        self.assertEqual(result["status"], "completed")
        requests = self.calls
        self.assertEqual([item[0] for item in requests], ["POST", "POST", "GET", "GET", "GET", "GET"])
        self.assertEqual(requests[0][2], requests[1][2])
        self.assertEqual(requests[1][2]["size"], "720P")
        self.assertEqual(requests[2][1], requests[3][1])
        self.assertEqual(requests[4][1], requests[5][1])
        self.assertIsNone(requests[5][3])
        self.assertEqual(Path(result["files"][0]).read_bytes(), b"video")
        retry_events = [event["data"] for event in self.events if event["event"] == "media_task" and "diagnostic" in event["data"]]
        self.assertEqual({event["diagnostic"]["stage"] for event in retry_events}, {"submit", "query", "download"})
        self.assertTrue(all(event["id"] == task["id"] for event in retry_events))

    def test_busy_exhaustion_allows_explicit_retry_of_same_request_only(self):
        from core.multimedia.runtime import SUBMIT_BACKOFF
        self.release_backoff.set()
        self.create_responses = [({"error": {"code": "video_queue_full"}}, 503)] * (len(SUBMIT_BACKOFF) + 1)
        task = self.submit("video_generate")
        failed = self.wait(task["id"])
        self.assertEqual(failed["status"], "not_submitted")
        self.assertTrue(self.runtime.public(failed)["can_retry"])
        self.assertEqual(len(self.calls), len(SUBMIT_BACKOFF) + 1)
        retry = {"id": task["id"], "conversation_id": "one", "updated_at": failed["updated_at"]}
        with self.assertRaisesRegex(ValueError, "此对话"):
            self.host.dispatch("media.retry", {**retry, "conversation_id": "two"})
        self.host.dispatch("media.retry", {**retry, "updated_at": task["updated_at"]})
        self.assertEqual(len(self.calls), len(SUBMIT_BACKOFF) + 1)
        revision = self.host.chat_tasks("one")[0]["revision"]
        retry = {"id": task["id"], "conversation_id": "one", "expected_revision": revision}
        self.host.dispatch("media.retry", {**retry, "expected_revision": revision - 1})
        self.assertEqual(len(self.calls), len(SUBMIT_BACKOFF) + 1)
        self.host.dispatch("media.retry", retry)
        self.host.dispatch("media.retry", retry)
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        self.assertGreater(self.host.chat_tasks("one")[0]["revision"], revision)
        self.assertEqual([item[2] for item in self.calls if item[0] == "POST"], [self.calls[0][2]] * (len(SUBMIT_BACKOFF) + 2))
        self.assertEqual([item["id"] for item in self.host.records("one")], [task["id"]])

    def test_video_refresh_and_missing_file_use_saved_remote_job(self):
        task = self.submit("video_generate")
        record = self.wait(task["id"])
        path = Path(record["files"][0])
        path.unlink()
        params = {"conversation_id": "one", "id": task["id"]}
        self.assertTrue(self.host.chat_tasks("one")[0]["can_redownload"])
        self.assertEqual(self.host.dispatch("media.preview", {**params, "path": str(path)}), {"missing": True})

        self.runtime.save(record, status="unknown", files=[], sources=[])
        self.query_responses = [({"status": "queued", "progress": 0}, 200)]
        before = len(self.calls)
        self.assertTrue(self.host.chat_tasks("one")[0]["can_refresh"])
        pending = self.host.dispatch("media.refresh", params)
        self.assertEqual((pending["status"], pending["phase"], pending["progress"]), ("paused", "queued", None))
        self.assertEqual([call[0] for call in self.calls[before:]], ["GET"])

        self.runtime.save(record, status="completed", files=[str(path)],
                          sources=[{"path": str(path), "url": self.base + "/output.mp4?expired", "name": path.name}])
        self.download_responses = [403]
        before = len(self.calls)
        self.host.dispatch("media.resume", {**params, "missing_file": str(path)})
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        requests = [(method, urlsplit(url).path) for method, url, *_ in self.calls[before:]]
        self.assertEqual(requests, [("GET", "/output.mp4"), ("GET", "/agnesapi"), ("GET", "/output.mp4")])
        self.assertEqual(path.read_bytes(), b"video")
        self.assertFalse(path.with_suffix(".mp4.part").exists())

    def test_older_video_snapshot_includes_creation_anchor_beyond_recent_limit(self):
        record = {"conversation_id": "one", "capability": "video_generate", "status": "completed",
                  "files": [], "run_id": "old", "tool_call_id": "make-video"}
        for index in range(101):
            identity = str(index)
            self.service.store.put_state("media:" + identity, {**record, "id": identity, "created_at": index,
                                         "run_id": "old" if index == 0 else "new"})
        self.assertNotIn("0", {task["id"] for task in self.host.chat_tasks("one")})
        messages = [{"role": "assistant", "meta": {"turn_id": "old"},
                     "tool_calls": [{"id": "make-video", "function": {"name": "media_create"}}]}]
        self.assertEqual([(task["id"], task["tool_call_id"], task["revision"])
                          for task in self.host.chat_tasks("one", messages, include_recent=False)], [("0", "make-video", 0)])

    def test_stop_during_safe_submit_backoff_does_not_restart(self):
        self.create_responses = [({"error": {"code": "video_queue_full"}}, 503)]
        task = self.submit("video_generate")
        self.assertTrue(self.entered_backoff.wait(3))
        self.runtime.stop(task["id"], "one")
        self.assertEqual(self.wait(task["id"])["status"], "cancelled")
        self.host.recover()
        self.assertEqual(len(self.calls), 1)

    def test_reopen_continues_safe_submit_backoff_with_original_credentials(self):
        self.create_responses = [({"error": {"code": "video_queue_full"}}, 503)]
        task = self.submit("video_generate")
        self.assertTrue(self.entered_backoff.wait(3))
        self.service.jobs.close()
        self.assertEqual(self.runtime.get(task["id"])["status"], "queued")
        self.assertEqual(len(self.calls), 1)
        self.service.jobs = DesktopJobs(self.service)
        self.host.release()
        self.host = DesktopMedia(self.service)
        self.runtime = self.host.acquire()
        self.host.recover()
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 2)

    def test_restart_does_not_submit_under_changed_credentials(self):
        self.create_responses = [({"error": {"code": "video_queue_full"}}, 503)]
        task = self.submit("video_generate")
        self.assertTrue(self.entered_backoff.wait(3))
        self.service.jobs.close()
        self.service.jobs = DesktopJobs(self.service)
        self.host.release()
        self.host = DesktopMedia(self.service)
        self.runtime = self.host.acquire()
        self.config.channels[0]["api_key"] = "changed-key"
        self.host.recover()
        self.assertEqual(self.runtime.get(task["id"])["status"], "not_submitted")
        self.assertEqual(len(self.calls), 1)
        saved = self.runtime.get(task["id"])
        self.config.channels[0]["api_key"] = "private-test-key"
        self.runtime.retry(task["id"], "one", saved["updated_at"])
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 2)

    def test_reopen_resumes_query_but_never_recreates_accepted_video(self):
        self.pending = True
        task = self.submit("video_generate")
        deadline = time.monotonic() + 3
        while not any(item[1].startswith('/agnesapi') for item in self.calls) and time.monotonic() < deadline:
            time.sleep(.02)
        self.service.jobs.close()
        self.assertEqual(self.runtime.get(task["id"])["status"], "running")
        self.pending = False
        self.service.jobs = DesktopJobs(self.service)
        self.host.release()
        self.host = DesktopMedia(self.service)
        self.runtime = self.host.acquire()
        self.host.recover()
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 1)

    def test_provider_contracts_and_input_validation(self):
        image = self.root / "one" / "reference.png"
        image.write_bytes(PNG)
        task = self.submit(inputs=[str(image)])
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        body = self.calls[0][2]
        self.assertNotIn("image", body)
        self.assertTrue(body["extra_body"]["image"][0].startswith("data:image/png;base64,"))
        self.assertNotIn("response_format", body)
        with self.assertRaisesRegex(ValueError, "HTTP"):
            self.submit("video_generate", inputs=[str(image)], identity="local-video")
        with self.assertRaisesRegex(ValueError, "工作目录"):
            self.submit(inputs=[str(self.root / "two" / "private.png")], identity="other-file")
        task = self.submit("video_generate", "ark", inputs=[str(image)], options={"seconds": 6}, identity="ark")
        self.assertEqual(self.wait(task["id"])["status"], "completed")
        body = next(item[2] for item in self.calls if item[1] == "/api/v3/contents/generations/tasks")
        self.assertEqual(body["duration"], 6)
        self.assertEqual(body["content"][1]["type"], "image_url")

    def test_flash_constraints_reject_before_submission_and_known_usage_survives_disable(self):
        from core.multimedia import validate_binding
        profile = self.profile(model="agnes-video-2.5-flash")
        adapter = Adapter(profile)
        for inputs, options, message in [([], {"size": "1080P"}, "720P"), ([], {"size": " "}, "720P"),
            ([self.base + "/reference.png"] * 6, {}, "5 张"), ([], {"seconds": 3}, "4–12")]:
            with self.assertRaisesRegex(ValueError, message):
                adapter.prepare("video_generate", "咖啡短片", inputs, options, str(self.root / "one"))
        self.assertEqual(self.calls, [])
        prepared = adapter.prepare("video_generate", "咖啡短片", [self.base + "/reference.png"] * 5,
                                   {"seconds": 4, "size": "720P"}, str(self.root / "one"))
        self.assertEqual(len(prepared["body"]["images"]), 5)
        binding = {"adapter": "agnes", "channel_id": "media", "model": profile["model"]}
        with self.assertRaisesRegex(ValueError, "只支持视频生成"):
            validate_binding("image_generate", binding)
        self.config.set_media({"enabled": True, "bindings": {"video_generate": binding}})
        lease = self.host.bind_run()
        try:
            create = next(tool["handler"] for tool in lease.tools() if tool["name"] == "media_create")
            result = create("video_generate", "咖啡短片", options={"size": "1080p"}, workspace_dir=str(self.root / "one"),
                            _context={"conversation_id": "one", "logical_execution_id": "invalid"})
            self.assertEqual(result["status"], "error")
            self.assertIs(result["submitted"], False)
            self.assertFalse(self.calls)
        finally:
            lease.close()
        chat_profile = {**profile, "model_name": profile["model"]}
        self.assertFalse(self.host.model_usage(chat_profile)["chat_supported"])
        self.config.set_skill_enabled("multimedia", False)
        self.assertFalse(self.host.model_usage(chat_profile)["chat_supported"])
        self.assertEqual(self.host.actions(), [])
        self.assertIsNone(self.host.model_usage({**chat_profile, "model_name": "my-video-chat-model"})["chat_supported"])
        self.assertTrue(self.host.model_usage({**chat_profile, "model_name": "dual-model", "discovery": {"endpoints": ["/v1/chat/completions", "/v1/videos"]}})["chat_supported"])
        self.assertFalse(self.host.model_usage({**chat_profile, "discovery": {"endpoints": ["/v1/chat/completions"]}})["chat_supported"])

    def test_draft_validation_uses_current_binding_without_saving_or_calling_provider(self):
        self.config.channels[0]["base_url"] = "https://apihub.agnes-ai.com/v1"
        runtime = self.host.acquire(existing=True)
        try:
            original = self.config.get("skill_configs")
            binding = {"adapter": "openai", "channel_id": "media", "model": "agnes-video-2.5-flash"}
            invalid = runtime.dispatch("media.validate", {"bindings": {"video_generate": binding}})
            self.assertFalse(invalid["video_generate"]["valid"])
            self.assertEqual(invalid["video_generate"]["suggested_adapter"], "agnes")
            corrected = runtime.dispatch("media.validate", {"bindings": {"video_generate": {**binding, "adapter": "agnes"}}})
            self.assertTrue(corrected["video_generate"]["valid"])
            self.assertEqual(corrected["video_generate"]["message"], "")
            self.assertEqual(self.config.get("skill_configs"), original)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.host.records(), [])
            usage = self.host.model_usage({"base_url": "https://apihub.agnes-ai.com/v1", "model_name": "agnes-image-2.5-flash"})
            self.assertFalse(usage["chat_supported"])
            self.config.set_media({"enabled": True, "bindings": {"video_generate": {}}})
            self.assertFalse(runtime.config()["binding_status"]["video_generate"]["valid"])
            lease = self.host.bind_run()
            self.assertEqual(lease.profiles, {})
            lease.close()
        finally:
            self.host.release()

    def test_video_understanding_saves_text_and_retired_capabilities_reject_new_tasks(self):
        task = self.submit("video_understand", "openai", inputs=[self.base + "/movie.mp4"])
        result = self.wait(task["id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(Path(result["files"][0]).read_text(encoding="utf-8"), "解析结果")
        for kind in ("audio_generate", "audio_understand", "image_understand"):
            with self.assertRaisesRegex(ValueError, "不支持"):
                self.submit(kind, "openai", identity=kind)

    def test_off_state_releases_only_after_run_and_job_leases(self):
        self.config.set_media({"enabled": True, "bindings": {"image_generate": {"adapter": "agnes", "model": "original", "channel_id": "media"}}})
        lease = self.host.bind_run()
        self.config.set_media({"enabled": True, "bindings": {"image_generate": {"adapter": "agnes", "model": "changed", "channel_id": "media"}}})
        self.assertEqual(lease.profiles["image_generate"]["model"], "original")
        manager = SkillManager(str(self.root / "one"), auto_load=False)
        before = manager.tool_registry.definitions("execution")
        lease.register(manager)
        self.assertEqual(before, manager.tool_registry.definitions("execution"))
        self.assertIn("media_create", [item["name"] for item in manager.tool_registry.search("生成图片")])
        self.assertIn("media_task", [item["name"] for item in manager.tool_registry.search("video progress")])
        self.config.set_skill_enabled("multimedia", False)
        self.host.reconcile()
        self.assertIsNone(self.host.bind_run())
        self.assertFalse(self.host.dispatch("media.config", {})["enabled"])
        self.assertIs(self.host.instance, self.runtime)
        lease.close()
        self.host.release()
        self.assertIsNone(self.host.instance)

    def test_receipt_recovers_after_missing_final_state(self):
        for capability in ("image_generate", "video_generate"):
            with self.subTest(capability=capability):
                count = len([item for item in self.calls if item[0] == "POST"])
                task = self.submit(capability, identity=capability)
                record = self.wait(task["id"])
                record.update(status="submitting", files=[])
                for key in ("remote_id", "provider_task_id", "sources", "receipt"):
                    record.pop(key, None)
                self.service.store.put_state("media:" + record["id"], record)
                restarted = DesktopMedia(self.service)
                runtime = restarted.acquire()
                try:
                    restarted.recover()
                    deadline = time.monotonic() + 5
                    while record["id"] in runtime.active and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertEqual(runtime.get(record["id"])["status"], "completed")
                    self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), count + 1)
                finally:
                    restarted.release()

    def test_restart_recovers_older_active_records_without_replaying_uncertain_submissions(self):
        for index in range(105):
            identity = f"recovery-{index}"
            self.service.store.put_state("media:" + identity, {"id": identity, "status": "submitting",
                "created_at": index, "conversation_id": "one", "workspace": str(self.root / "one"),
                "destination": str(self.root / "one" / "media" / identity), "capability": "video_generate", "profile": {}})
        DesktopMedia(self.service).recover()
        self.assertEqual(self.service.store.get_state("media:recovery-0")["status"], "unknown")
        self.assertEqual(self.service.store.get_state("media:recovery-104")["status"], "unknown")
        self.assertFalse(self.calls)

    def test_job_start_failure_does_not_leave_a_queued_task_or_lease(self):
        leases = self.host.leases
        with patch.object(self.service.jobs, "submit", side_effect=RuntimeError("shutting down")):
            with self.assertRaisesRegex(RuntimeError, "shutting down"):
                self.submit()
        self.assertEqual(self.host.records()[0]["status"], "cancelled")
        self.assertEqual(self.host.leases, leases)
        self.assertFalse(self.runtime.active)
        self.assertFalse(self.calls)

    def test_interrupted_generation_response_is_unknown_and_never_resubmitted(self):
        submit = Adapter.submit
        def interrupted(adapter, *args):
            submit(adapter, *args)
            raise MediaError("response interrupted", uncertain=True)
        with patch.object(Adapter, "submit", interrupted):
            task = self.submit("image_generate", "openai")
            self.assertEqual(self.wait(task["id"])["status"], "unknown")
        self.submit("image_generate", "openai")
        self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 1)

    def test_pending_configuration_survives_restart_and_continues_once_in_its_conversation(self):
        lease = self.host.bind_run()
        create = next(tool["handler"] for tool in lease.tools() if tool["name"] == "media_create")
        context = {"conversation_id": "one", "logical_execution_id": "pending"}
        args = dict(capability="image_generate", prompt="保留这条需求", workspace_dir=str(self.root / "one"), _context=context)
        task = create(**args)
        self.assertEqual(task["status"], "needs_configuration")
        self.assertEqual(create(**args)["id"], task["id"])
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.host.records()), 1)
        self.config.set_media({"enabled": True, "bindings": {"image_generate": {"channel_id": "media", "adapter": "openai", "model": "configured"}}})
        restarted = DesktopMedia(self.service)
        self.assertEqual(restarted.records()[0]["status"], "needs_configuration")
        runtime = restarted.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "此对话"):
                runtime.continue_pending(task["id"], "two")
            runtime.continue_pending(task["id"], "one")
            runtime.continue_pending(task["id"], "one")
            deadline = time.monotonic() + 8
            while task["id"] in runtime.active and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(runtime.get(task["id"])["status"], "completed")
            self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 1)
            self.assertEqual(runtime.get(task["id"])["request"]["prompt"], "保留这条需求")
        finally:
            restarted.release()
            lease.close()

    def test_real_worker_discovers_media_and_returns_saved_file(self):
        requests_seen = []
        class ChatHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests_seen.append(request)
                tools = [message for message in request["messages"] if message["role"] == "tool"]
                if not tools:
                    name, args = "tool_search", {"query": "multimedia image generate task"}
                elif not any('"task_id"' in str(message.get("content")) for message in tools):
                    name, args = "media_create", {"capability": "image_generate", "prompt": "测试图片"}
                else:
                    media = next(json.loads(message["content"]) for message in reversed(tools) if '"task_id"' in str(message.get("content")))
                    if media.get("status") == "completed":
                        delta = {"content": f"已生成：[测试图片](<{media['files'][0]}>)"}
                        name = None
                    else:
                        name, args = "media_task", {"task_id": media["task_id"], "wait_seconds": 5}
                if name:
                    delta = {"tool_calls": [{"index": 0, "id": "call-" + str(len(requests_seen)), "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                payload = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode())
                self.wfile.write(("data: " + json.dumps({"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if name else "stop"}]}) + "\n\ndata: [DONE]\n\n").encode())
        chat = ThreadingHTTPServer(("127.0.0.1", 0), ChatHandler)
        threading.Thread(target=chat.serve_forever, daemon=True).start()
        client = ServiceClient(self.root / "worker")
        try:
            settings = client.call("settings.save_model", name="Chat", model_name="fixture", api_key="chat-key", base_url=f"http://127.0.0.1:{chat.server_port}/v1")
            client.call("settings.permission", enabled=True)
            client.call("skills.enable", name="multimedia", enabled=True)
            config = client.call("media.config")
            client.call("media.save", revision=config["revision"], bindings={"image_generate": {"channel_id": "media-fixture", "model": "agnes-image", "adapter": "agnes"}},
                        services=[{"channel_id": "media-fixture", "display_name": "Media", "base_url": self.base, "api_key": "private-media-key"}])
            conversation = client.call("conversation.create")
            image = Path(conversation["meta"]["workspace_dir"]) / "input.png"
            image.write_bytes(PNG)
            import uuid
            client.call("conversation.send", conversation_id=conversation["id"], request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                        text="修改这张图片", model_id=settings["selected_model_id"], attachments=[{"path": str(image), "name": image.name}])
            result = client.wait_run(conversation["id"])
            self.assertEqual(result["run"]["status"], "completed", result["run"])
            records = client.call("media.tasks", conversation_id=conversation["id"])
            self.assertEqual(len(records), 1, result["messages"])
            self.assertEqual(records[0]["status"], "completed")
            self.assertTrue(any(item.get("file_references") for item in result["render_messages"]))
            self.assertNotIn("private-media-key", json.dumps(result["messages"]))
            first = requests_seen[0]
            self.assertNotIn("media_create", [(item.get("function") or {}).get("name") for item in first["tools"]])
            self.assertIn("media_create", [(item.get("function") or {}).get("name") for item in requests_seen[1]["tools"]])
            self.assertFalse(any(part.get("type") == "image_url" for item in first["messages"] if isinstance(item.get("content"), list) for part in item["content"]))
        finally:
            client.close()
            chat.shutdown()
            chat.server_close()

    def test_video_task_survives_chat_completion_and_service_restart(self):
        self.pending = True
        requests_seen = []
        class ChatHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests_seen.append(body)
                tools = [message for message in body["messages"] if message["role"] == "tool"]
                user = next(message for message in reversed(body["messages"]) if message["role"] == "user")
                ordinary = "普通问题" in str(user["content"])
                if ordinary:
                    name, args = None, {}
                elif not tools:
                    name, args = "tool_search", {"query": "media_create multimedia video generate"}
                elif len(tools) == 1:
                    name, args = "media_create", {"capability": "video_generate", "prompt": "测试视频", "options": {"size": "720p"}}
                else:
                    name, args = None, {}
                delta = {"content": "普通回答。" if ordinary else "视频正在后台生成，完成后在这里播放。"} if name is None else {"tool_calls": [{
                    "index": 0, "id": "call-" + str(len(requests_seen)), "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for chunk, finish in ((delta, None), ({}, "tool_calls" if name else "stop")):
                    payload = {"id": "fixture", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": chunk, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")

        chat = ThreadingHTTPServer(("127.0.0.1", 0), ChatHandler)
        threading.Thread(target=chat.serve_forever, daemon=True).start()
        client = ServiceClient(self.root / "video-worker")
        try:
            settings = client.call("settings.save_model", name="Chat", model_name="fixture", api_key="chat-key", base_url=f"http://127.0.0.1:{chat.server_port}/v1")
            client.call("settings.permission", enabled=True)
            config = client.call("media.config")
            client.call("media.save", revision=config["revision"], enabled=True,
                        bindings={"video_generate": {"channel_id": "video-fixture", "model": "agnes-video-2.5-flash", "adapter": "agnes"}},
                        services=[{"channel_id": "video-fixture", "display_name": "Video", "base_url": self.base, "api_key": "private-video-key"}])
            cid = client.call("conversation.create")["id"]
            import uuid
            client.call("conversation.send", conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                        text="生成一个短片", model_id=settings["selected_model_id"])
            result = client.wait_run(cid)
            self.assertEqual(result["run"]["status"], "completed")
            self.assertIn("视频正在后台生成", json.dumps(result["render_messages"], ensure_ascii=False))
            def accepted(value):
                return value.get("event") == "media_task" and value.get("conversation_id") == cid and value["data"].get("remote_id") == "video-2"
            event = next((value for value in client.events if accepted(value)), None) or client.until(accepted, timeout=5)
            pending = event["data"]
            self.assertEqual(pending["run_id"], result["run"]["id"])
            calls = [call["id"] for message in result["render_messages"] for call in message.get("tool_calls") or []
                     if call["function"]["name"] == "media_create"]
            self.assertEqual(calls, [pending["tool_call_id"]])
            self.assertIsInstance(pending["revision"], int)
            self.assertEqual(len(requests_seen), 3)
            client.call("conversation.send", conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex,
                        text="普通问题", model_id=settings["selected_model_id"])
            next_run = client.wait_run(cid)
            self.assertEqual(next_run["run"]["status"], "completed")
            self.assertNotEqual(next_run["run"]["id"], pending["run_id"])
            self.assertIn("普通回答", json.dumps(next_run["render_messages"], ensure_ascii=False))
            self.assertEqual(next_run["media_tasks"][0]["status"], "running")
            self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 1)
        finally:
            client.close()
            chat.shutdown()
            chat.server_close()

        self.pending = False
        client = ServiceClient(self.root / "video-worker")
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                snapshot = client.call("conversation.get", conversation_id=cid)
                if snapshot["media_tasks"][0]["status"] == "completed":
                    break
                time.sleep(.1)
            self.assertEqual([task["id"] for task in snapshot["media_tasks"]], [pending["id"]])
            self.assertEqual(snapshot["media_tasks"][0]["status"], "completed")
            self.assertEqual(Path(snapshot["media_tasks"][0]["files"][0]).read_bytes(), b"video")
            self.assertEqual(len([item for item in self.calls if item[0] == "POST"]), 1)
        finally:
            client.close()


class MultimediaServiceTests(unittest.TestCase):
    def test_incompatible_saved_binding_can_be_replaced_then_unbound(self):
        from core.desktop_config import DesktopConfigManager

        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            with patch("core.config_manager.get_app_data_dir", return_value=directory):
                config = DesktopConfigManager(migrate_legacy=False)
            channels = config.get_model_channels()
            channels.extend([
                {"channel_id": "agnes", "display_name": "Agnes", "api_key": "test-key",
                 "base_url": "https://apihub.agnes-ai.com/v1", "models": [
                     {"model_name": "agnes-image-2.1-flash"}, {"model_name": "agnes-image-2.5-flash"},
                     {"model_name": "agnes-video-2.5"}, {"model_name": "agnes-video-2.5-flash"},
                 ]},
                {"channel_id": "video", "display_name": "Video", "api_key": "test-key",
                 "base_url": "https://ark.cn-beijing.volces.com/api/v3", "models": [{"model_name": "video-demo"}]},
            ])
            config.set_model_channels(channels)
            invalid = {"channel_id": "agnes", "model": "agnes-image-2.5-flash", "adapter": "openai"}
            config.set_skill_config("multimedia", {"bindings": json.dumps({"video_understand": invalid})})
            config.set_skill_enabled("multimedia", True)
            client = ServiceClient(directory)
            try:
                data = client.call("media.config")
                self.assertFalse(data["binding_status"]["video_understand"]["valid"])
                self.assertIn("图片生成", data["binding_status"]["video_understand"]["message"])
                self.assertNotIn("agnes-image-2.1-flash", {item["model"] for item in data["options"]["video_generate"]})
                self.assertNotIn("agnes-video-2.5", {item["model"] for item in data["options"]["image_generate"]})
                options = data["options"]["video_understand"]
                replacement = {"channel_id": "video", "model": "video-demo", "adapter": "ark"}
                self.assertEqual(options, [replacement])
                rejected = client.call("media.save", revision=data["revision"], bindings=data["bindings"])
                self.assertFalse(rejected["saved"])
                self.assertIn("video_understand", rejected["binding_issues"])
                self.assertEqual(client.call("media.config")["bindings"], data["bindings"])
                data = client.call("media.save", revision=data["revision"], bindings={"video_understand": replacement})
                self.assertEqual(data["bindings"]["video_understand"], replacement)
                data = client.call("media.save", revision=data["revision"], enabled=False, bindings={})
                self.assertFalse(data["enabled"])
                self.assertEqual(data["bindings"], {})
            finally:
                client.close()

    def test_legacy_configuration_migrates_once_and_explicit_skill_state_wins(self):
        from core.desktop_config import DesktopConfigManager
        from core.multimedia import configuration
        legacy = {"image_generate": {"adapter": "openai", "channel_id": "old", "model": "image"},
                  "audio_understand": {"adapter": "openai", "channel_id": "old", "model": "transcribe"}}
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
                value = {"experimental_multimedia": {"enabled": True, "bindings": legacy}}
                if explicit:
                    value["disabled_skills"] = ["multimedia"]
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with patch("core.config_manager.get_app_data_dir", return_value=directory):
                    config = DesktopConfigManager(migrate_legacy=False)
                    self.assertEqual(configuration(config)["bindings"], legacy)
                    self.assertEqual(configuration(config)["enabled"], not explicit)
                    self.assertNotIn("experimental_multimedia", config.config)
                    config.set_skill_enabled("multimedia", False)
                    reloaded = DesktopConfigManager(migrate_legacy=False)
                    self.assertFalse(configuration(reloaded)["enabled"])
                    self.assertEqual(configuration(reloaded)["bindings"], legacy)
                    self.assertNotIn("experimental_multimedia", json.loads(path.read_text(encoding="utf-8")))

    def test_model_configuration_save_failure_preserves_settings_and_emits_no_success(self):
        from core.desktop_config import DesktopConfigManager
        from core.multimedia.runtime import MediaRuntime
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            with patch("core.config_manager.get_app_data_dir", return_value=directory):
                config = DesktopConfigManager(migrate_legacy=False)
            events = []
            service = SimpleNamespace(config=config, publish=events.append)
            host = SimpleNamespace(s=service, lock=threading.RLock(), records=lambda: [])
            runtime = MediaRuntime(host)
            before = copy.deepcopy(config.config)
            request = {"revision": runtime.config()["revision"], "enabled": True,
                       "bindings": {"image_generate": {"adapter": "openai", "model": "image", "channel_id": "new"}},
                       "services": [{"channel_id": "new", "display_name": "New", "base_url": "https://example.test/v1", "api_key": "private"}]}
            with patch.object(config, "_write_config_or_raise", side_effect=OSError("disk unavailable")):
                with self.assertRaisesRegex(OSError, "disk unavailable"):
                    runtime.save_config(request)
            self.assertEqual(config.config, before)
            self.assertEqual(events, [])

    def test_skill_routes_and_configuration_survive_disable_and_restart(self):
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            client = ServiceClient(directory)
            try:
                self.assertFalse(client.call("settings.get")["multimedia_enabled"])
                self.assertEqual(client.call("media.config")["bindings"], {})
                with self.assertRaisesRegex(RuntimeError, "设置页面不存在"):
                    client.call("settings.page", page="experiments")
                skill = next(item for item in client.call("skills.inventory") if item["name"] == "multimedia")
                self.assertFalse(skill["enabled"])
                self.assertEqual(skill["source_type"], "bundled_plugin")
                settings = client.call("media.config")
                saved = client.call("media.save", revision=settings["revision"], enabled=True,
                                    bindings={"image_generate": {"adapter": "agnes", "model": "agnes-image-2.0-flash", "channel_id": "agnes-fixture"}},
                                    services=[{"channel_id": "agnes-fixture", "display_name": "Agnes", "base_url": "https://apihub.agnes-ai.com/v1", "api_key": "private-media-key"}])
                self.assertNotIn("private-media-key", json.dumps(saved))
                models = client.call("settings.save_model", channel_id="agnes-fixture", model_name="agnes-video-2.5-flash",
                                     name="Agnes", base_url="https://apihub.agnes-ai.com/v1")
                model = next(item for item in models["models"] if item["model_name"] == "agnes-video-2.5-flash")
                self.assertFalse(model["chat_supported"])
                conversation = client.call("conversation.create")
                import uuid
                request = {"conversation_id": conversation["id"], "request_id": uuid.uuid4().hex, "message_id": uuid.uuid4().hex,
                           "text": "不应发往聊天接口", "model_id": model["id"]}
                with self.assertRaisesRegex(RuntimeError, "选择对话模型"):
                    client.call("conversation.send", **request)
                self.assertEqual(client.call("conversation.get", conversation_id=conversation["id"])["messages"], [])
                with self.assertRaisesRegex(RuntimeError, "已更新"):
                    client.call("media.save", revision=settings["revision"], bindings={})
                client.call("skills.enable", name="multimedia", enabled=False)
                self.assertFalse(client.call("settings.get")["multimedia_enabled"])
                with self.assertRaisesRegex(RuntimeError, "选择对话模型"):
                    client.call("conversation.send", **request)
            finally:
                client.close()
            client = ServiceClient(directory)
            try:
                client.call("skills.enable", name="multimedia", enabled=True)
                self.assertEqual(client.call("media.config")["bindings"], saved["bindings"])
            finally:
                client.close()

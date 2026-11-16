"""Multimedia tasks reuse desktop jobs and storage, independently of renderer life."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from . import ADAPTERS, CAPABILITIES, KNOWN_MODELS, normalize, snapshot, validate_binding, quick_actions, binding_error
from .adapters import Adapter, MediaError, safe_message

log = logging.getLogger("multimedia")
ACTIVE = {"queued", "submitting", "running", "downloading"}


class RunLease:
    def __init__(self, runtime, profiles):
        self.runtime, self.profiles, self.closed = runtime, profiles, False

    def close(self):
        if not self.closed:
            self.closed = True
            self.runtime.host.release()
            self.profiles.clear()

    def register(self, manager):
        if self.closed:
            return
        manager.skill_source_kinds["multimedia"] = "optional"
        for spec in self.tools():
            if spec["name"] not in manager.tools:
                manager._register_explicit_tool_export("multimedia", spec)

    def tools(self):
        def submit(capability, prompt="", inputs=None, options=None, workspace_dir=None, _context=None):
            if self.closed:
                raise ValueError("此轮多媒体能力已释放。")
            profile = self.profiles.get(capability)
            if not profile:
                return {"status": "not_configured", "error": "请在设置 → 多媒体中为此能力选择模型。"}
            return self.runtime.submit(capability, prompt, inputs or [], options or {}, profile, workspace_dir, _context or {})

        def task(task_id="", action="get", wait_seconds=0, _context=None):
            cid = str((_context or {}).get("conversation_id") or (_context or {}).get("session_id") or "")
            if not cid:
                raise ValueError("缺少对话身份。")
            if not task_id:
                return {"tasks": [self.runtime.public(item) for item in self.runtime.host.records(cid)]}
            record = self.runtime.get(task_id, cid)
            if action == "resume":
                record = self.runtime.resume(task_id, cid)
            elif action == "stop_waiting":
                record = self.runtime.stop(task_id, cid)
            elif action != "get":
                raise ValueError("不支持此任务操作。")
            deadline = time.monotonic() + max(0, min(30, float(wait_seconds)))
            while record["status"] in ACTIVE and time.monotonic() < deadline:
                if (_context or {}).get("abort_check", lambda: False)():
                    break
                time.sleep(.2)
                record = self.runtime.get(task_id, cid)
            return self.runtime.public(record)

        task.__name__ = "media_task"
        return [
            {"name": "media_create", "handler": submit, "description":
             "Generate/edit images, generate video or speech, understand images/videos, or transcribe audio using configured multimedia models. "
             "Returns a durable task_id; use media_task to wait. Never resubmit an unknown outcome. "
             "inputs are workspace file paths or HTTP URLs. Video understanding and Agnes video reference images require publicly accessible URLs. "
             "Video generation inputs are reference images. Speech synthesis creates AI-generated voice. Link completed local files in the final answer.",
             "search_hint": "multimedia media image video audio generate edit understand analyze transcription speech picture movie 图片 视频 音频 生成 解析 配音 转写",
             "parameters": {"type": "object", "properties": {
                 "capability": {"type": "string", "enum": list(CAPABILITIES)}, "prompt": {"type": "string"},
                 "inputs": {"type": "array", "items": {"type": "string"}},
                 "options": {"type": "object", "properties": {"size": {"type": "string"}, "seconds": {"type": "integer"},
                     "aspect_ratio": {"type": "string"}, "voice": {"type": "string"}, "speed": {"type": "number"}, "language": {"type": "string"}},
                     "additionalProperties": False}}, "required": ["capability"], "additionalProperties": False},
             "requires_workspace": True, "read_only": False, "destructive": True, "should_defer": True},
            {"name": "media_task", "handler": task, "description":
             "List this conversation's multimedia tasks or inspect/wait up to 30 seconds for one. resume only queries the saved remote task or retries download; "
             "it never creates a new generation. stop_waiting pauses local polling; the remote generation may continue. Return completed files using local Markdown links.",
             "search_hint": "multimedia media image video audio task status wait resume download progress 图片 视频 音频 任务 状态 恢复 下载",
             "parameters": {"type": "object", "properties": {"task_id": {"type": "string"},
                 "action": {"type": "string", "enum": ["get", "resume", "stop_waiting"]},
                 "wait_seconds": {"type": "number", "minimum": 0, "maximum": 30}}, "additionalProperties": False},
             "read_only": False, "destructive": False, "should_defer": True},
        ]


class MediaRuntime:
    def __init__(self, host):
        self.host, self.s = host, host.s
        self.lock = threading.RLock()
        self.active = {}

    def bind_run(self):
        return RunLease(self, snapshot(self.s.config))

    def config(self):
        from core.desktop_settings import DesktopSettings
        value = normalize(self.s.config.get("experimental_multimedia"))
        source_channels = self.s.config.get_model_channels()
        channels = [{key: item[key] for key in ("channel_id", "display_name", "base_url", "provider_type")}
                    | {"configured": bool(item["api_key"]), "models": [model["model_name"] for model in item["models"]]}
                    for item in source_channels]
        records = self.host.records()
        statuses = {}
        for kind, binding in value["bindings"].items():
            channel = next((item for item in channels if item["channel_id"] == binding.get("channel_id")), {})
            reason = binding_error(kind, binding, next((item for item in source_channels if item["channel_id"] == binding.get("channel_id")), {}))
            if reason:
                statuses[kind] = {"label": "配置需修正", "message": reason, "valid": False}
                continue
            recent = next((item for item in records if item["capability"] == kind and all(
                item.get("profile", {}).get(key) == binding.get(key) for key in ("channel_id", "model", "adapter"))
                and item.get("profile", {}).get("base_url") == channel.get("base_url")), None)
            statuses[kind] = {"label": "已配置，未验证"} if not recent else {
                "label": "最近调用成功" if recent["status"] == "completed" else "最近调用失败" if recent["status"] in {"failed", "download_failed"} else recent["message"],
                "message": recent["message"], "updated_at": recent.get("updated_at"), "status": recent["status"]}
        return {"bindings": value["bindings"], "channels": channels, "adapters": ADAPTERS,
                "known_models": KNOWN_MODELS, "actions": quick_actions(value["bindings"], source_channels), "binding_status": statuses,
                "revision": DesktopSettings.revision(value), "tasks": [self.public(item) for item in self.host.records()]}

    def save_config(self, p):
        with self.host.lock:
            current = self.config()
            if p.get("revision") != current["revision"]:
                raise ValueError("多媒体配置已更新，当前编辑已保留。请重新载入后合并。")
            bindings = {key: validate_binding(key, value) for key, value in p.get("bindings", {}).items()}
            bindings = {key: value for key, value in bindings.items() if value}
            channels = self.s.config.get_model_channels()
            # Credentials always use the host's existing protected model channels.
            for value in p.get("services", []):
                identity = str(value.get("channel_id") or "")
                if not identity:
                    raise ValueError("新服务缺少标识。")
                previous = next((item for item in channels if item["channel_id"] == identity), None)
                if previous:
                    raise ValueError("服务标识已存在，请在模型与服务中编辑。")
                from .adapters import http_url
                base = http_url(str(value.get("base_url") or "").strip())
                name, key = str(value.get("display_name") or "").strip(), str(value.get("api_key") or "").strip()
                if not name or not key:
                    raise ValueError("请填写服务名称与 API Key。")
                channels.append({"channel_id": identity, "display_name": name, "base_url": base,
                                 "api_key": key, "provider_type": "openai", "models": []})
            by_id = {item["channel_id"]: item for item in channels}
            for kind, binding in bindings.items():
                channel = by_id.get(binding["channel_id"])
                if not channel or channel["provider_type"] != "openai":
                    raise ValueError("请选择 OpenAI 兼容服务。")
                reason = binding_error(kind, binding, channel)
                if reason:
                    raise ValueError(reason)
                Adapter({**binding, "base_url": channel["base_url"], "api_key": channel["api_key"]})
            with self.s.config.batch_save():
                if p.get("services"):
                    self.s.config.set_model_channels(channels, self.s.config.get_selected_model_id())
                self.s.config.set("experimental_multimedia", {"enabled": True, "bindings": bindings})
            log.info("media_configuration_saved bindings=%s", sorted(bindings))
            self.s.publish({"event": "settings_changed"})
            return self.config()

    def dispatch(self, method, p):
        if method == "media.config":
            return self.config()
        if method == "media.save":
            return self.save_config(p)
        if method == "media.tasks":
            return [self.public(item) for item in self.host.records(str(p.get("conversation_id") or ""))]
        if method == "media.resume":
            return self.public(self.resume(str(p["id"]), cid=str(p.get("conversation_id") or ""), remote_id=str(p.get("remote_id") or "")))
        if method == "media.stop":
            return self.public(self.stop(str(p["id"]), cid=str(p.get("conversation_id") or "")))
        raise ValueError("不支持此多媒体操作。")

    def get(self, identity, cid=""):
        record = self.s.store.get_state("media:" + identity)
        if not record or (cid and record["conversation_id"] != cid):
            raise ValueError("此对话中没有该多媒体任务。")
        return record

    @staticmethod
    def public(record):
        from core.desktop_media import DesktopMedia
        return DesktopMedia.public(record)

    def save(self, record, **changes):
        with self.lock:
            latest = self.s.store.get_state("media:" + record["id"], {})
            if latest.get("job_id"):
                record["job_id"] = latest["job_id"]
            record.update(changes, updated_at=time.time())
            self.s.store.put_state("media:" + record["id"], record)
            self.s.publish({"event": "media_task", "conversation_id": record["conversation_id"], "data": self.public(record)})
        log.info("media_task_state id=%s session=%s run=%s capability=%s adapter=%s phase=%s", record["id"], record["conversation_id"],
                 record.get("run_id", ""), record["capability"], record.get("profile", {}).get("adapter", ""), record["status"])

    def submit(self, capability, prompt, inputs, options, profile, workspace, context):
        if capability not in CAPABILITIES:
            raise ValueError("不支持此多媒体能力。")
        cid = str(context.get("conversation_id") or context.get("session_id") or "")
        if not cid or not workspace:
            raise ValueError("多媒体任务需要当前对话及工作目录。")
        root = Path(workspace).resolve()
        if root != self.s.files.workspace(cid):
            raise ValueError("任务工作目录与当前对话不一致。")
        protected = {str(Path(value).resolve()).casefold() for message in context.get("current_messages_snapshot", [])
                     for key in ("local_only_audio_files", "transcription_only_audio_files")
                     for value in (message.get("meta") or {}).get(key, [])}
        if any(not value.startswith(("http://", "https://")) and str((root / value).resolve()).casefold() in protected for value in inputs):
            raise ValueError("此音频原先标记为仅在本地处理，不能发送给多媒体服务。请明确重新添加需要转写的素材。")
        execution_id = str(context.get("logical_execution_id") or context.get("tool_call_id") or "")
        if not execution_id:
            raise ValueError("缺少工具执行编号，无法可靠提交任务。")
        identity = uuid.uuid5(uuid.NAMESPACE_URL, "media:" + cid + ":" + execution_id).hex
        request = {"capability": capability, "prompt": prompt, "inputs": inputs, "options": options}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            previous = self.s.store.get_state("media:" + identity)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("相同执行编号不能提交不同的多媒体请求。")
                return self.public(previous)
            adapter = Adapter(profile)
            prepared = adapter.prepare(capability, prompt, inputs, options, str(root))
            destination = root / "media" / identity
            if not destination.resolve().is_relative_to(root):
                raise ValueError("成果目录不能指向当前工作目录以外的位置。")
            destination.mkdir(parents=True, exist_ok=True)
            record = {"id": identity, "conversation_id": cid, "workspace": str(root), "destination": str(destination),
                      "fingerprint": fingerprint, "request": request, "profile": {key: value for key, value in profile.items() if key != "api_key"},
                      "model": profile["model"], "service_name": profile["service_name"], "capability": capability,
                      "run_id": str(context.get("run_id") or context.get("turn_id") or ""),
                      "created_at": time.time(), "status": "queued", "message": "等待提交", "files": []}
            self.save(record)
            self.start(record, adapter, prepared)
            return self.public(self.get(identity))

    def start(self, record, adapter, prepared=None):
        identity = record["id"]
        with self.lock:
            if identity in self.active:
                return
            cancelled = threading.Event()
            self.active[identity] = cancelled
            self.host.acquire(existing=True)
            def run(job_cancelled, progress):
                self.execute(record, adapter, prepared, cancelled, job_cancelled, progress)
                result = self.public(self.get(identity))
                if result["status"] != "completed":
                    from core.desktop_jobs import JobCancelled
                    if result["status"] in {"paused", "cancelled"}:
                        raise JobCancelled(result["message"])
                    raise MediaError(result["message"])
                return result
            def settled(job):
                try:
                    current = self.get(identity)
                    if current["status"] == "queued":
                        self.save(current, status="cancelled", message="已停止，尚未向服务提交。")
                finally:
                    with self.lock:
                        self.active.pop(identity, None)
                    self.host.release()
            try:
                job = self.s.jobs.submit(CAPABILITIES[record["capability"]], run, on_settled=settled)
                # Do not overwrite a newer state if a small task finished immediately.
                current = self.get(identity)
                self.save(current, job_id=job["job_id"])
            except BaseException:
                self.active.pop(identity, None)
                self.host.release()
                self.save(self.get(identity), status="cancelled" if prepared is not None else "paused",
                          message="后台操作未能启动，尚未提交新请求；已有输入与成果记录已保留。")
                raise

    def execute(self, record, adapter, prepared, cancelled, job_cancelled, progress):
        destination = Path(record["destination"])
        stop = lambda: cancelled.is_set() or job_cancelled.is_set()
        try:
            if prepared is not None:
                if stop():
                    self.save(record, status="cancelled", message="已停止，尚未向服务提交。")
                    return
                self.save(record, status="submitting", message="正在提交…")
                progress("正在提交多媒体任务…")
                receipt = adapter.submit(prepared, destination)
                record.update(receipt)
                receipt_path = destination / "receipt.json"
                Adapter.write_bytes(receipt_path, json.dumps(receipt, ensure_ascii=False).encode())
                self.save(record, receipt=str(receipt_path), status="running" if receipt.get("remote_id") else "downloading",
                          message="服务已接受，等待生成…" if receipt.get("remote_id") else "正在保存成果…")
            if record.get("remote_id") and not record.get("sources"):
                deadline, errors = time.monotonic() + 600, 0
                while True:
                    if stop() or time.monotonic() >= deadline:
                        self.save(record, status="paused", message="已停止等待；远端生成可能继续，可恢复查询。")
                        return
                    try:
                        result = adapter.poll(record["remote_id"])
                        errors = 0
                    except MediaError as exc:
                        if not exc.retryable or errors >= 5:
                            raise
                        errors += 1
                        self.save(record, message=f"查询暂时失败，正在重试（{errors}/5）…")
                        progress(record["message"])
                        cancelled.wait(min(2 ** errors, 30))
                        continue
                    if result["status"] == "failed":
                        self.save(record, status="failed", message=result["error"])
                        return
                    if result["status"] == "generated":
                        self.save(record, sources=result["sources"], status="downloading", message="生成完成，正在保存成果…")
                        break
                    self.save(record, status="running", progress=result.get("progress"), message="正在生成视频…")
                    progress(record["message"])
                    cancelled.wait(2)
            if record.get("text") is not None:
                target = destination / "transcript.txt" if record["capability"] == "audio_understand" else destination / "analysis.md"
                Adapter.write_bytes(target, record["text"].encode("utf-8"))
                record["files"] = [str(target)]
            for source in record.get("sources", []):
                if stop():
                    self.save(record, status="paused", message="成果已生成，已停止下载；可恢复下载。")
                    return
                path = adapter.download(source, destination)
                source["path"] = path
                if path not in record["files"]:
                    record["files"].append(path)
                self.save(record, status="downloading", message="正在保存成果…")
            if not record.get("files"):
                raise MediaError("没有可交付的成果，任务记录已保留。")
            self.save(record, status="completed", message="已保存到对话工作目录。", progress=100)
            progress("成果已保存")
        except Exception as exc:
            status = ("download_failed" if record.get("sources") else "paused" if record.get("remote_id") else
                      "unknown" if record["status"] == "submitting" and (not isinstance(exc, MediaError) or exc.uncertain) else "failed")
            reason = safe_message(exc, adapter.profile.get("api_key", ""))
            suffix = {"unknown": "提交结果未确认，请先核查服务后台，勿重复提交。", "download_failed": "生成成果仍保留，可重试下载。",
                      "paused": "任务编号已保留，可恢复查询。", "failed": "输入与任务记录已保留。"}[status]
            self.save(record, status=status, message=reason + " " + suffix)
            progress(record["message"])
            log.error("media_task_failed id=%s phase=%s error_type=%s", record["id"], status, type(exc).__name__)

    def resume(self, identity, cid="", remote_id=""):
        with self.lock:
            record = self.get(identity, cid)
            if identity in self.active or record["status"] == "completed":
                return record
            root = self.s.files.workspace(record["conversation_id"])
            destination = Path(record["destination"]).resolve()
            if root != Path(record["workspace"]).resolve() or destination != root / "media" / identity:
                raise ValueError("对话工作目录已改变，请恢复原目录后继续该任务。")
            receipt_path = destination / "receipt.json"
            if receipt_path.is_file() and not record.get("remote_id") and not record.get("sources"):
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                record.update({key: receipt[key] for key in ("remote_id", "provider_task_id", "sources", "text", "files") if key in receipt})
            if remote_id:
                if record["status"] != "unknown" or record["capability"] != "video_generate":
                    raise ValueError("只有提交结果未确认的视频任务可以补充远端编号。")
                if len(remote_id) > 256 or any(ch.isspace() for ch in remote_id):
                    raise ValueError("远端任务编号无效。")
                record["remote_id"] = remote_id
            if not record.get("remote_id") and not record.get("sources") and not record.get("text") and not record.get("files"):
                raise ValueError("没有可恢复的远端编号或成果，请先核对服务后台；不会重新发起生成。")
            profile = dict(record["profile"])
            channel = next((item for item in self.s.config.get_model_channels() if item["channel_id"] == profile["channel_id"]), {})
            if channel.get("base_url") != profile["base_url"]:
                raise ValueError("原服务已删除或地址改变，请恢复原服务后继续。")
            profile["api_key"] = channel.get("api_key", "")
            adapter = Adapter(profile)
            self.save(record, status="downloading" if record.get("sources") else "running", message="正在恢复…")
            self.start(record, adapter)
            return self.get(identity)

    def stop(self, identity, cid=""):
        with self.lock:
            record = self.get(identity, cid)
            event = self.active.get(identity)
            if event and record["status"] in ACTIVE:
                event.set()
                self.save(record, message="正在停止等待，已发出的请求将先保存结果；远端生成可能继续。")
                # The worker owns terminal state; a delayed stop never undoes completion.
            return record

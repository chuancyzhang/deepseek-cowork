"""Multimedia tasks reuse desktop jobs and storage, independently of renderer life."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from core.desktop_media import ACTIVE, BUSY, RESUMABLE, UNCONFIRMED, has_media_result

from . import ADAPTERS, CAPABILITIES, SKILL_CAPABILITIES, KNOWN_MODELS, configuration, snapshot, validate_binding, binding_error, model_usage
from .adapters import Adapter, MediaError, safe_message

log = logging.getLogger("multimedia")
SUBMIT_BACKOFF = (5, 10, 20)
DOWNLOAD_BACKOFF = (2, 4, 8)


class InvalidMediaRequest(ValueError):
    pass


def tool_snapshot(result):
    if result["capability"] == "video_generate":
        if result["status"] in ACTIVE and not result.get("stop_requested"):
            result["message"] = "视频正在后台处理，聊天任务卡会自动更新，无需查询进度。"
        elif result["can_retry"]:
            result["message"] += "用户可在原任务卡重试生成，不需要重新发送需求。"
    return result


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
            try:
                if capability not in SKILL_CAPABILITIES:
                    raise InvalidMediaRequest("不支持此多媒体能力。")
                result = self.runtime.submit(capability, prompt, [] if inputs is None else inputs,
                                             {} if options is None else options, self.profiles.get(capability), workspace_dir, _context or {})
                return tool_snapshot(result)
            except InvalidMediaRequest as exc:
                message = f"未提交：{exc}"
                return {"status": "error", "submitted": False, "error": message, "content": message}

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
            return tool_snapshot(self.runtime.public(record))

        task.__name__ = "media_task"
        return [
            {"name": "media_create", "handler": submit, "description":
             "Generate/edit images, generate videos, or understand videos using the multimedia skill's configured models. "
             "Returns a durable task_id; use media_task to wait. Never resubmit an unknown outcome. "
             "inputs are workspace file paths or HTTP URLs. Video understanding and Agnes video reference images require publicly accessible URLs. "
             "Video generation inputs are reference images. Missing configuration creates a pending task; ask the user to configure the skill and click Continue. Do not resubmit. Link completed local files in the final answer.",
             "search_hint": "multimedia media image video generate edit understand analyze picture movie 图片 视频 生成 解析",
             "parameters": {"type": "object", "properties": {
                 "capability": {"type": "string", "enum": list(SKILL_CAPABILITIES)}, "prompt": {"type": "string"},
                 "inputs": {"type": "array", "items": {"type": "string"}},
                 "options": {"type": "object", "properties": {"size": {"type": "string"}, "seconds": {"type": "integer"},
                     "aspect_ratio": {"type": "string"}},
                     "additionalProperties": False}}, "required": ["capability"], "additionalProperties": False},
             "requires_workspace": True, "read_only": False, "destructive": True, "should_defer": True},
            {"name": "media_task", "handler": task, "description":
             "List this conversation's multimedia tasks or inspect/wait up to 30 seconds for one. resume only queries the saved remote task or retries download; "
             "it never creates a new generation. stop_waiting pauses local polling; the remote generation may continue. Return completed files using local Markdown links.",
             "search_hint": "multimedia media image video task status wait resume download progress 图片 视频 任务 状态 恢复 下载",
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

    def validate(self, p):
        """Local draft checks only: never persist, submit, or probe a provider."""
        channels = {item["channel_id"]: item for item in self.s.config.get_model_channels()}
        for item in p.get("services", []):
            if item.get("channel_id") not in channels:
                channels[item.get("channel_id")] = item
        result = {}
        for kind, binding in p.get("bindings", {}).items():
            if kind not in CAPABILITIES or not isinstance(binding, dict):
                continue
            channel = channels.get(binding.get("channel_id"), {})
            reason = binding_error(kind, binding, channel)
            usage = model_usage({**channel, "model_name": binding.get("model")}, {kind: binding})
            suggested = usage.get("adapter") if kind in usage.get("capabilities", []) and binding.get("adapter") != usage.get("adapter") else None
            result[kind] = {"valid": not reason, "message": reason, "suggested_adapter": suggested}
        return result

    def config(self):
        from core.desktop_settings import DesktopSettings
        value = configuration(self.s.config)
        visible = {key: item for key, item in value["bindings"].items() if key in SKILL_CAPABILITIES}
        source_channels = self.s.config.get_model_channels()
        channels = [{key: item[key] for key in ("channel_id", "display_name", "base_url", "provider_type")}
                    | {"configured": bool(item["api_key"]), "models": [model["model_name"] for model in item["models"]]}
                    for item in source_channels]
        records = self.host.records()
        checks = self.validate({"bindings": visible})
        statuses = {}
        for kind, binding in visible.items():
            channel = next((item for item in channels if item["channel_id"] == binding.get("channel_id")), {})
            reason = checks[kind]["message"]
            if reason:
                statuses[kind] = {"label": "配置需修正", "message": reason, "valid": False}
                continue
            recent = next((item for item in records if item["capability"] == kind and all(
                item.get("profile", {}).get(key) == binding.get(key) for key in ("channel_id", "model", "adapter"))
                and item.get("profile", {}).get("base_url") == channel.get("base_url")), None)
            if recent:
                recent = self.public(recent)
            statuses[kind] = {"label": "已配置，未验证"} if not recent else {
                "label": "最近调用成功" if recent["status"] == "completed" else "最近调用失败" if recent["status"] in {"failed", "download_failed"} else recent["message"],
                "message": recent["message"], "updated_at": recent.get("updated_at"), "status": recent["status"]}
        return {"enabled": value["enabled"], "bindings": visible, "channels": channels, "adapters": ADAPTERS,
                "known_models": KNOWN_MODELS, "binding_status": statuses,
                "revision": DesktopSettings.revision(value), "tasks": [self.public(item) for item in self.host.records()]}

    def save_config(self, p):
        with self.host.lock:
            current = self.config()
            if p.get("revision") != current["revision"]:
                raise ValueError("多媒体配置已更新，当前编辑已保留。请重新载入后合并。")
            bindings = {key: validate_binding(key, value) for key, value in p.get("bindings", {}).items()}
            bindings = {key: value for key, value in bindings.items() if value}
            if set(bindings) - set(SKILL_CAPABILITIES):
                raise ValueError("此技能仅支持图片生成、视频生成和视频理解。")
            enabled = p.get("enabled", current["enabled"]) is True
            if enabled and not bindings:
                raise ValueError("请至少选择一个模型后保存并启用。")
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
                retained = {key: value for key, value in configuration(self.s.config)["bindings"].items() if key not in SKILL_CAPABILITIES}
                self.s.config.set_skill_config("multimedia", {"bindings": json.dumps({**retained, **bindings}, ensure_ascii=False)})
                self.s.config.set_skill_enabled("multimedia", enabled)
            log.info("media_configuration_saved bindings=%s", sorted(bindings))
            self.s.management.skill_change("updated", ["multimedia"])
            self.s.publish({"event": "settings_changed"})
            return self.config()

    def dispatch(self, method, p):
        if method == "media.config":
            return self.config()
        if method == "media.validate":
            return self.validate(p)
        if method == "media.save":
            return self.save_config(p)
        if method == "media.tasks":
            return [self.public(item) for item in self.host.records(str(p.get("conversation_id") or ""))]
        cid = str(p.get("conversation_id") or "")
        if not cid:
            raise ValueError("缺少对话身份。")
        if method == "media.resume":
            if p.get("remote_id"):
                raise ValueError("只使用原任务的服务回执恢复，不接受手填编号。")
            return self.public(self.resume(str(p["id"]), cid))
        if method == "media.retry":
            return self.public(self.retry(str(p["id"]), cid, p.get("updated_at")))
        if method == "media.continue":
            return self.public(self.continue_pending(str(p["id"]), cid))
        if method == "media.stop":
            return self.public(self.stop(str(p["id"]), cid))
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
            if latest.get("stop_requested"):
                record["stop_requested"] = True
            record.update(changes, updated_at=time.time())
            self.s.store.put_state("media:" + record["id"], record)
            self.s.publish({"event": "media_task", "conversation_id": record["conversation_id"], "data": self.public(record)})
        log.info("media_task_state id=%s session=%s run=%s capability=%s adapter=%s phase=%s", record["id"], record["conversation_id"],
                 record.get("run_id", ""), record["capability"], record.get("profile", {}).get("adapter", ""), record["status"])

    def submit(self, capability, prompt, inputs, options, profile, workspace, context):
        if capability not in SKILL_CAPABILITIES:
            raise ValueError("不支持此多媒体能力。")
        cid = str(context.get("conversation_id") or context.get("session_id") or "")
        if not cid or not workspace:
            raise ValueError("多媒体任务需要当前对话及工作目录。")
        root = Path(workspace).resolve()
        if root != self.s.files.workspace(cid):
            raise ValueError("任务工作目录与当前对话不一致。")
        if (not isinstance(prompt, str) or not isinstance(options, dict) or not isinstance(inputs, list)
                or not all(isinstance(value, str) and value for value in inputs)):
            raise InvalidMediaRequest("生成要求、素材或参数格式无效。")
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
            if not self.host.enabled:
                raise ValueError("多媒体技能已停用，请启用后开始新任务。")
            try:
                adapter = Adapter(profile) if profile else None
                prepared = self.prepare(adapter, request, root) if adapter else None
            except (ValueError, OSError) as exc:
                log.info("media_validation_rejected id=%s session=%s capability=%s", identity, cid, capability)
                raise InvalidMediaRequest(safe_message(exc, (profile or {}).get("api_key", ""))) from None
            destination = root / "media" / identity
            if not destination.resolve().is_relative_to(root):
                raise ValueError("成果目录不能指向当前工作目录以外的位置。")
            destination.mkdir(parents=True, exist_ok=True)
            record = {"id": identity, "conversation_id": cid, "workspace": str(root), "destination": str(destination),
                      "fingerprint": fingerprint, "request": request, "profile": self.saved_profile(profile) if profile else {},
                      "model": (profile or {}).get("model", ""), "service_name": (profile or {}).get("service_name", ""), "capability": capability,
                      "run_id": str(context.get("run_id") or context.get("turn_id") or
                                    getattr(context.get("execution_authorization"), "run_id", "")),
                      "created_at": time.time(), "status": "queued", "message": "等待提交", "files": []}
            if not profile:
                record.update(status="needs_configuration", message="需求已保留。请在多媒体技能中选择模型，返回后点击继续任务。")
            self.save(record)
            if adapter:
                self.start(record, adapter, prepared)
            return self.public(self.get(identity))

    @staticmethod
    def prepare(adapter, request, root):
        return adapter.prepare(request["capability"], request["prompt"], request["inputs"], request["options"], str(root))

    def continue_pending(self, identity, cid):
        with self.lock:
            if not cid:
                raise ValueError("缺少对话身份。")
            record = self.get(identity, cid)
            if record["status"] != "needs_configuration":
                return record
            if not self.host.enabled:
                raise ValueError("请先启用多媒体技能。")
            root = self.task_workspace(record)
            profile = snapshot(self.s.config).get(record["capability"])
            if not profile:
                raise ValueError("请先为此任务配置模型，原需求已保留。")
            adapter = Adapter(profile)
            prepared = self.prepare(adapter, record["request"], root)
            return self.restart_job(record, adapter, prepared, profile=self.saved_profile(profile),
                                    model=profile["model"], service_name=profile["service_name"])

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
                if result["status"] == "completed":
                    return result
                if cancelled.is_set() or job_cancelled.is_set():
                    from core.desktop_jobs import JobCancelled
                    raise JobCancelled("已保留任务进度。")
                raise MediaError(result["message"])
            def settled(_job, job_cancelled):
                with self.lock:
                    try:
                        if cancelled.is_set() or (job_cancelled.is_set() and not job_cancelled.for_shutdown):
                            current = self.get(identity)
                            if current["status"] in ACTIVE:
                                self.pause(current)
                    finally:
                        self.active.pop(identity, None)
                        self.host.release()
            try:
                job = self.s.jobs.submit(CAPABILITIES[record["capability"]], run, on_settled=settled)
            except BaseException:
                self.active.pop(identity, None)
                self.host.release()
                status = "paused" if prepared is None else "not_submitted" if record["capability"] == "video_generate" else "cancelled"
                self.save(self.get(identity), status=status, message="后台任务未能启动，需求和进度已保留。")
                raise
            current = self.get(identity)
            self.save(current, job_id=job["job_id"])

    def pause(self, record):
        if has_media_result(record):
            self.save(record, status="paused", message="已停止等待，可以继续原任务。")
        elif record["status"] == "queued":
            self.save(record, status="cancelled", message="已停止，尚未向服务提交。")
        else:
            self.save(record, status="unknown", message=UNCONFIRMED)

    @staticmethod
    def retry_wait(seconds, cancelled, job_cancelled):
        deadline = time.monotonic() + seconds
        while not cancelled.is_set() and not job_cancelled.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            cancelled.wait(min(remaining, .25))

    def report_retry(self, record, exc, stage, progress, message, **changes):
        self.save(record, message=message, error_detail=self.error_detail(exc, stage), **changes)
        progress(f"{message}（{exc}）")

    @staticmethod
    def error_detail(exc, stage, secret=""):
        detail = {"stage": stage, "message": safe_message(exc, secret)}
        if isinstance(exc, MediaError):
            if exc.http_status is not None:
                detail["http_status"] = exc.http_status
            if exc.code:
                detail["code"] = exc.code
            if exc.request_id:
                detail["request_id"] = exc.request_id
        return detail

    def execute(self, record, adapter, prepared, cancelled, job_cancelled, progress):
        destination = Path(record["destination"])
        stop = lambda: cancelled.is_set() or job_cancelled.is_set()
        stage = "submit" if prepared is not None else "query"
        try:
            if prepared is not None:
                retries = record.get("submit_retries", 0)
                while True:
                    with self.lock:
                        if stop():
                            return
                        self.save(record, status="submitting", message="正在提交…")
                    progress("正在提交多媒体任务…")
                    try:
                        receipt = adapter.submit(prepared, destination)
                        break
                    except MediaError as exc:
                        safe_retry = record["capability"] == "video_generate" and exc.retryable and not exc.uncertain
                        if not safe_retry or retries >= len(SUBMIT_BACKOFF):
                            raise
                        delay = SUBMIT_BACKOFF[retries]
                        retries += 1
                        self.report_retry(record, exc, stage, progress,
                                          f"生成服务繁忙，正在重试（{retries}/{len(SUBMIT_BACKOFF)}）…", status="queued", submit_retries=retries)
                        self.retry_wait(delay, cancelled, job_cancelled)
                record.update(receipt)
                receipt_path = destination / "receipt.json"
                Adapter.write_bytes(receipt_path, json.dumps(receipt, ensure_ascii=False).encode())
                self.save(record, receipt=str(receipt_path), status="running" if receipt.get("remote_id") else "downloading",
                          message="服务已接受，等待生成…" if receipt.get("remote_id") else "正在保存成果…", error_detail=None)
            if record.get("remote_id") and not record.get("sources"):
                stage = "query"
                deadline, errors = time.monotonic() + 600, 0
                while True:
                    if stop():
                        return
                    if time.monotonic() >= deadline:
                        self.save(record, status="paused", message="生成时间比预期长，可以继续等待原任务。")
                        return
                    try:
                        result = adapter.poll(record["remote_id"])
                        errors = 0
                    except MediaError as exc:
                        if not exc.retryable or errors >= 5:
                            raise
                        errors += 1
                        self.report_retry(record, exc, stage, progress, "连接暂时中断，正在继续等待…")
                        self.retry_wait(min(2 ** errors, 30), cancelled, job_cancelled)
                        continue
                    if result["status"] == "failed":
                        self.save(record, status="failed", message="服务未能生成成果，需求已保留。",
                                  error_detail={"stage": stage, "message": result["error"]})
                        return
                    if result["status"] == "generated":
                        self.save(record, sources=result["sources"], status="downloading", message="生成完成，正在保存成果…", error_detail=None)
                        break
                    self.save(record, status="running", progress=result.get("progress"), message="正在生成视频…", error_detail=None)
                    progress(record["message"])
                    self.retry_wait(2, cancelled, job_cancelled)
            stage = "download"
            if record.get("text") is not None:
                target = destination / "transcript.txt" if record["capability"] == "audio_understand" else destination / "analysis.md"
                Adapter.write_bytes(target, record["text"].encode("utf-8"))
                record["files"] = [str(target)]
            for source in record.get("sources", []):
                for attempt in range(len(DOWNLOAD_BACKOFF) + 1):
                    if stop():
                        return
                    try:
                        path = adapter.download(source, destination)
                        break
                    except MediaError as exc:
                        if not exc.retryable or attempt == len(DOWNLOAD_BACKOFF):
                            raise
                        self.report_retry(record, exc, stage, progress, "正在重试保存成果…")
                        self.retry_wait(DOWNLOAD_BACKOFF[attempt], cancelled, job_cancelled)
                source["path"] = path
                if path not in record["files"]:
                    record["files"].append(path)
                self.save(record, status="downloading", message="正在保存成果…")
            if not record.get("files"):
                raise MediaError("没有可交付的成果，任务记录已保留。")
            self.save(record, status="completed", message="已保存到对话工作目录。", progress=100, error_detail=None)
            progress("成果已保存")
        except Exception as exc:
            detail = self.error_detail(exc, stage, adapter.profile["api_key"])
            if record.get("sources"):
                status, message = "download_failed", "成果已生成，但暂未保存到本机。"
            elif record.get("remote_id"):
                status, message = "paused", "暂时无法获取生成进度，原任务已保留。"
            elif stage == "submit" and (not isinstance(exc, MediaError) or exc.uncertain):
                status, message = "unknown", UNCONFIRMED
            elif stage == "submit" and isinstance(exc, MediaError) and exc.retryable and record["capability"] == "video_generate":
                status, message = "not_submitted", BUSY
            else:
                status, message = "failed", "服务未能完成任务，需求已保留。"
            self.save(record, status=status, message=message, error_detail=detail)
            progress(message)
            log.warning("media_task_failed id=%s session=%s stage=%s status=%s http_status=%s code=%s request_id=%s error_type=%s",
                        record["id"], record["conversation_id"], stage, status, detail.get("http_status"), detail.get("code", ""),
                        detail.get("request_id", ""), type(exc).__name__)

    def task_workspace(self, record):
        root = self.s.files.workspace(record["conversation_id"])
        destination = Path(record["destination"]).resolve()
        if root != Path(record["workspace"]).resolve() or destination != root / "media" / record["id"]:
            raise ValueError("对话工作目录已改变，请恢复原目录后继续该任务。")
        return root

    def saved_profile(self, profile):
        return {key: value for key, value in profile.items() if key != "api_key"} | {
            "connection_identity": self.host.test_identity({**profile, "model_name": profile["model"]})}

    def original_adapter(self, record, *, auto_submit=False):
        profile = dict(record["profile"])
        channel = next((item for item in self.s.config.get_model_channels() if item["channel_id"] == profile["channel_id"]), {})
        if channel.get("base_url") != profile["base_url"]:
            raise ValueError("原服务已删除或地址改变，请恢复原服务后继续。")
        profile["api_key"] = channel.get("api_key", "")
        if auto_submit and profile.get("connection_identity") != self.saved_profile(profile)["connection_identity"]:
            raise ValueError("无法确认服务凭据与提交时一致，未自动提交。")
        return Adapter(profile)

    @staticmethod
    def read_receipt(record):
        receipt_path = Path(record["destination"]) / "receipt.json"
        if receipt_path.is_file() and not has_media_result(record):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or not has_media_result(receipt):
                raise ValueError("无法读取已保存的多媒体回执。")
            return {key: receipt[key] for key in ("remote_id", "provider_task_id", "sources", "text", "files") if key in receipt}
        return {}

    def restart_job(self, record, adapter, prepared=None, **changes):
        status = "queued" if prepared is not None else "downloading" if not record.get("remote_id") or record.get("sources") else "running"
        self.save(record, status=status, message="正在继续原任务…", stop_requested=False, **changes)
        self.start(record, adapter, prepared)
        return self.get(record["id"])

    def recover_interrupted(self, record):
        identity = record["id"]
        with self.lock:
            if identity in self.active:
                return
            can_submit = record["status"] == "queued"
            try:
                root = self.task_workspace(record)
                record.update(self.read_receipt(record))
                if record.get("stop_requested"):
                    self.pause(record)
                elif has_media_result(record):
                    self.restart_job(record, self.original_adapter(record))
                elif can_submit and self.host.enabled and not record.get("remote_id"):
                    adapter = self.original_adapter(record, auto_submit=True)
                    self.restart_job(record, adapter, self.prepare(adapter, record["request"], root))
                elif can_submit:
                    self.save(record, status="not_submitted", message="多媒体已停用，尚未提交任务，需求已保留。")
                else:
                    self.save(record, status="unknown", message=UNCONFIRMED)
                log.info("media_recovered id=%s session=%s", identity, record["conversation_id"])
            except Exception as exc:
                if identity in self.active:
                    log.exception("media_recovery_save_failed id=%s", identity)
                    return
                if has_media_result(record):
                    status, message = "paused", "暂时无法继续原任务，已有进度已保留。"
                elif can_submit:
                    status, message = "not_submitted", "尚未提交任务，需求已保留。"
                else:
                    status, message = "unknown", UNCONFIRMED
                self.save(record, status=status, message=message, error_detail=self.error_detail(exc, "recovery"))
                log.warning("media_recovery_failed id=%s session=%s status=%s error_type=%s",
                            identity, record["conversation_id"], status, type(exc).__name__)

    def retry(self, identity, cid, updated_at):
        with self.lock:
            record = self.get(identity, cid)
            if identity in self.active or record["status"] != "not_submitted" or record["updated_at"] != updated_at:
                return record
            if not self.host.enabled:
                raise ValueError("请先启用多媒体，原需求已保留。")
            root = self.task_workspace(record)
            if record.get("remote_id") or has_media_result(record) or self.read_receipt(record):
                raise ValueError("此任务已有回执，不会重复生成。")
            adapter = self.original_adapter(record)
            prepared = self.prepare(adapter, record["request"], root)
            log.info("media_retry_requested id=%s session=%s", identity, cid)
            return self.restart_job(record, adapter, prepared, profile=self.saved_profile(adapter.profile), submit_retries=0, error_detail=None)

    def resume(self, identity, cid=""):
        with self.lock:
            record = self.get(identity, cid)
            if identity in self.active or record["status"] not in RESUMABLE:
                return record
            self.task_workspace(record)
            record.update(self.read_receipt(record))
            if not has_media_result(record):
                raise ValueError("服务未返回可继续的任务，原需求已保留。")
            return self.restart_job(record, self.original_adapter(record))

    def stop(self, identity, cid=""):
        with self.lock:
            record = self.get(identity, cid)
            event = self.active.get(identity)
            if event and record["status"] in ACTIVE:
                event.set()
                self.save(record, stop_requested=True, message="正在停止等待，已提交的生成可能继续。")
                # The worker owns terminal state; a delayed stop never undoes completion.
            return record

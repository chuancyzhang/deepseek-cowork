"""Host-owned lifecycle, configuration and durable records for the media plugin."""
import json
import logging
import threading
import copy
import hashlib
from pathlib import Path

log = logging.getLogger("desktop.media")
ACTIVE = frozenset({"queued", "submitting", "running", "downloading"})
RESUMABLE = frozenset({"paused", "download_failed", "unknown"})
UNCONFIRMED = "服务未确认接单，需求已保留。"
BUSY = "生成服务繁忙，尚未接单，需求已保留。"


def has_media_result(record):
    return bool(record.get("remote_id") and record.get("provider_task_id") or record.get("sources") or
                record.get("text") or record.get("files"))


class DesktopMedia:
    methods = frozenset({"media.config", "media.validate", "media.save", "media.tasks", "media.resume", "media.retry",
                         "media.continue", "media.stop", "media.refresh", "media.preview"})

    def __init__(self, service):
        self.s = service
        self.lock = threading.RLock()
        self.instance = None
        self.leases = 0
        service.config.multimedia_host = self

    def recover(self):
        records = self.records(active=True)
        if not records:
            return
        runtime = self.acquire(existing=True)
        try:
            for record in records:
                runtime.recover_interrupted(record)
        finally:
            self.release()

    @property
    def enabled(self):
        return self.s.config.is_skill_enabled("multimedia", default_enabled=False)

    def acquire(self, *, existing=False):
        with self.lock:
            if not existing and not self.enabled:
                raise ValueError("请在能力 → 技能中启用多媒体。")
            if self.instance is None:
                from core.multimedia.runtime import MediaRuntime
                self.instance = MediaRuntime(self)
                log.info("media_plugin_loaded")
            self.leases += 1
            return self.instance

    def release(self):
        with self.lock:
            self.leases = max(0, self.leases - 1)
            self.reconcile()

    def reconcile(self):
        with self.lock:
            if not self.enabled and self.leases == 0 and self.instance is not None:
                self.instance = None
                log.info("media_plugin_unloaded retained_records=true")

    def bind_run(self):
        with self.lock:
            if not self.enabled:
                return None
            runtime = self.acquire()
            try:
                return runtime.bind_run()
            except BaseException:
                self.release()
                raise

    def records(self, conversation_id="", limit=100, *, active=False, versioned=False, anchors=None):
        clauses, params = ["key LIKE 'media:%'"], []
        if active:
            clauses.append("json_extract(value, '$.status') IN (?, ?, ?, ?)")
            params.extend(sorted(ACTIVE))
        if conversation_id:
            clauses.append("json_extract(value, '$.conversation_id') = ?")
            params.append(conversation_id)
        if anchors is not None:
            turns, tasks = anchors
            matches = []
            if turns:
                matches.append("json_extract(value, '$.run_id') IN (" + ",".join("?" for _ in turns) + ")")
                params.extend(turns)
            if tasks:
                matches.append("key IN (" + ",".join("?" for _ in tasks) + ")")
                params.extend("media:" + task for task in tasks)
            if not matches:
                return []
            clauses.extend(["json_extract(value, '$.capability') = 'video_generate'", "(" + " OR ".join(matches) + ")"])
        page = " LIMIT ?" if not active and anchors is None else ""
        if page:
            params.append(max(1, min(500, int(limit))))
        with self.s.storage._connect() as db:
            return [dict(json.loads(value), revision=revision) if versioned else json.loads(value)
                    for value, revision in db.execute(
                "SELECT value, revision FROM desktop_view_state WHERE " + " AND ".join(clauses) +
                " ORDER BY json_extract(value, '$.created_at') DESC" + page, params)]

    def chat_tasks(self, cid, messages=(), *, include_recent=True):
        turns, tasks = set(), set()
        for message in messages:
            if any(((call.get("function") or {}).get("name") or call.get("name")) == "media_create"
                   for call in message.get("tool_calls") or ()):
                meta = message.get("meta") or {}
                turn = meta.get("turn_id") or meta.get("ui_turn_id")
                if turn:
                    turns.add(turn)
            result = message.get("result_obj")
            if message.get("role") == "tool" and isinstance(result, dict) and isinstance(result.get("task_id"), str):
                tasks.add(result["task_id"])
        recent = self.records(cid, versioned=True) if include_recent else []
        placed = self.records(cid, versioned=True, anchors=(turns, tasks))
        return [self.desktop_task(record) for record in {item["id"]: item for item in [*recent, *placed]}.values()]

    def dispatch(self, method, p):
        runtime = self.acquire(existing=True)
        try:
            if method in {"media.config", "media.validate"}:
                with self.lock:
                    return runtime.dispatch(method, p)
            return runtime.dispatch(method, p)
        finally:
            self.release()

    @staticmethod
    def test_identity(profile):
        # Include credentials and protocol in the digest; never persist their plaintext.
        provider = profile.get("provider_type") or "openai"
        profile = {**profile, "provider_type": provider,
                   "base_url": str(profile.get("base_url") or "").rstrip("/"),
                   "api_protocol": profile.get("api_protocol") or ("anthropic" if provider == "anthropic" else "chat_completions")}
        return hashlib.sha256(json.dumps([profile.get(key) or "" for key in
            ("channel_id", "base_url", "model_name", "provider_type", "api_protocol", "api_key")]).encode()).hexdigest()

    def connection_test(self, profile):
        return copy.deepcopy(self.s.config.get("model_connection_tests", {}).get(self.test_identity(profile)))

    def record_connection_test(self, profile, success, message):
        from datetime import datetime, timezone
        result = {"success": success, "message": message, "checked_at": datetime.now(timezone.utc).isoformat()}
        with self.lock:
            records = copy.deepcopy(self.s.config.get("model_connection_tests", {}))
            records[self.test_identity(profile)] = result
            self.s.config.set("model_connection_tests", records)
        return result

    def model_usage(self, profile, *, bindings=None):
        from core.llm.gateway_catalog import ENDPOINTS
        tested = self.connection_test(profile)
        if tested and tested["success"]:
            return {"chat_supported": True, "purpose_label": "对话"}
        identity = hashlib.sha256(json.dumps([profile.get(key) for key in ("channel_id", "base_url", "model_name")]).encode()).hexdigest()
        with self.lock:
            known = copy.deepcopy(self.s.config.get("model_usage", {}))
            if bindings is not None or self.enabled:
                from core.multimedia import model_usage, configuration
                current = configuration(self.s.config)["bindings"] if bindings is None else bindings
                if fact := model_usage(profile, current):
                    if bindings is None and known.get(identity) != fact:
                        known[identity] = fact
                        self.s.config.set("model_usage", known)
                    return fact
            if known.get(identity):
                return copy.deepcopy(known[identity])
        endpoints = (profile.get("discovery") or {}).get("endpoints") or []
        if any(endpoint in ENDPOINTS for endpoint in endpoints):
            return {"chat_supported": True, "purpose_label": "对话"}
        if endpoints:
            return {"chat_supported": False, "purpose_label": "专用接口", "reason": "此模型未声明支持对话接口，请在对应功能中使用。"}
        return {"chat_supported": None, "purpose_label": "服务未提供能力声明"}

    def actions(self):
        return []

    def configured_capabilities(self):
        if not self.enabled:
            return []
        from core.multimedia import configuration, binding_error, SKILL_CAPABILITIES
        channels = {item["channel_id"]: item for item in self.s.config.get_model_channels()}
        return [kind for kind, binding in configuration(self.s.config)["bindings"].items()
                if kind in SKILL_CAPABILITIES and not binding_error(kind, binding, channels.get(binding.get("channel_id")))]

    @staticmethod
    def public(record):
        result = {key: copy.deepcopy(record[key]) for key in (
            "id", "conversation_id", "run_id", "capability", "status", "message", "created_at", "updated_at", "model",
            "service_name", "remote_id", "provider_task_id", "progress", "files", "text", "job_id") if key in record}
        result["task_id"] = result["id"]
        result["text"] = result.get("text", "")[:16000]
        result["can_resume"] = record["status"] in RESUMABLE and (
            has_media_result(record) or (Path(record["destination"]) / "receipt.json").is_file())
        result["can_retry"] = record["status"] == "not_submitted"
        result["needs_configuration"] = record["status"] == "needs_configuration"
        if record["status"] in ACTIVE and record.get("stop_requested"):
            result.update(stop_requested=True, message="正在停止等待，已提交的生成可能继续。")
        if record.get("error_detail"):
            result["diagnostic"] = record["error_detail"]
        if record["status"] == "unknown" or (record["status"] == "paused" and record.get("remote_id") and not result["can_resume"]):
            result["status"] = "unknown"
            result["message"] = UNCONFIRMED
        return result

    @classmethod
    def desktop_task(cls, record, revision=None):
        result = cls.public(record)
        if record["capability"] != "video_generate":
            return result
        result.update(revision=record.get("revision") if revision is None else revision,
                      tool_call_id=record.get("tool_call_id"), phase=record.get("video_phase"),
                      can_refresh=cls.can_refresh_video(record),
                      can_redownload=any(source.get("url") or source.get("api_path") for source in record.get("sources", [])))
        if record["status"] == "failed" and result.get("diagnostic", {}).get("http_status") == 429:
            result.update(status="rate_limited", message="视频服务限制了调用频率，要求已保留。可在服务端核对用量。")
        return result

    @staticmethod
    def can_refresh_video(record):
        detail = record.get("error_detail") or {}
        return (record["capability"] == "video_generate" and
                (record["status"] == "unknown" or (record["status"] == "paused" and detail.get("stage") == "query")) and
                bool(record.get("remote_id") or (Path(record["destination"]) / "receipt.json").is_file()))

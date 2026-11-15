"""Host-owned lifecycle, configuration and durable records for the media plugin."""
import json
import logging
import threading

log = logging.getLogger("desktop.media")


class DesktopMedia:
    methods = frozenset({"media.config", "media.save", "media.tasks", "media.resume", "media.stop"})

    def __init__(self, service):
        self.s = service
        self.lock = threading.RLock()
        self.instance = None
        self.leases = 0
        service.config.multimedia_host = self
        # Recovery is host work: disabled plugins are not imported on startup.
        while unfinished := self.records(active=True):
            for record in unfinished:
                record["status"] = {"submitting": "unknown", "queued": "cancelled"}.get(record["status"], "paused")
                record["message"] = {"unknown": "提交结果未确认，请先核对服务后台，避免重复生成。",
                                     "cancelled": "应用退出时任务尚未提交，输入与记录已保留。"}.get(record["status"], "应用上次退出时任务未完成。记录已保留，可恢复查询或下载。")
                self.s.store.put_state("media:" + record["id"], record)

    @property
    def enabled(self):
        return (self.s.config.get("experimental_multimedia") or {}).get("enabled") is True

    def acquire(self, *, existing=False):
        with self.lock:
            if not existing and not self.enabled:
                raise ValueError("请先在实验功能中启用多媒体。")
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

    def records(self, conversation_id="", limit=100, *, active=False):
        clauses, params = ["key LIKE 'media:%'"], []
        if active:
            clauses.append("json_extract(value, '$.status') IN ('queued','submitting','running','downloading')")
        if conversation_id:
            clauses.append("json_extract(value, '$.conversation_id') = ?")
            params.append(conversation_id)
        params.append(max(1, min(500, int(limit))))
        with self.s.storage._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM desktop_view_state WHERE " + " AND ".join(clauses) +
                " ORDER BY json_extract(value, '$.created_at') DESC LIMIT ?", params)]

    def dispatch(self, method, p):
        runtime = self.acquire()
        try:
            return runtime.dispatch(method, p)
        finally:
            self.release()

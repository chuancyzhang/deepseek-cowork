"""Observable, cancellable UI operations independent of renderer lifetime."""
from concurrent.futures import ThreadPoolExecutor
import copy
import logging
import threading
import time
import uuid

log = logging.getLogger("desktop.jobs")
LOG_LIMIT = 300


class JobCancelled(RuntimeError):
    pass


class DesktopJobs:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="desktop-job")
        self.active = {}
        while unfinished := self.list(status="active"):
            for job in unfinished:
                reason = "应用上次退出时此操作未完成。已有结果已保留，可重新操作。"
                job.update(status="interrupted", message=reason, error=reason)
                self.save(job)

    def list(self, query="", status="", before="", limit=100):
        import json
        clauses, params = ["key LIKE 'job:%'"], []
        if status == "active":
            clauses.append("json_extract(value, '$.status') IN ('queued','running','stopping')")
        elif status:
            clauses.append("json_extract(value, '$.status') = ?")
            params.append(str(status))
        if before:
            clauses.append("key < ?")
            params.append("job:" + str(before))
        if str(query).strip():
            clauses.append("""instr(lower(COALESCE(json_extract(value, '$.title'), '') || ' ' ||
                COALESCE(json_extract(value, '$.message'), '') || ' ' ||
                COALESCE(json_extract(value, '$.error'), '') || ' ' ||
                COALESCE(json_extract(value, '$.logs'), '')), lower(?)) > 0""")
            params.append(str(query).strip())
        params.append(max(1, min(100, int(limit))))
        with self.service.storage._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM desktop_view_state WHERE " + " AND ".join(clauses) + " ORDER BY key DESC LIMIT ?", params)]

    def get(self, identity):
        job = self.service.store.get_state("job:" + identity)
        if not job:
            raise ValueError("操作记录不存在。")
        return job

    def save(self, job):
        with self.lock:
            now = time.time()
            previous = self.service.store.get_state("job:" + job["id"], {})
            entries = previous.get("logs", [])
            entry = {"at": now, "status": job["status"], "message": str(job.get("message") or job.get("error") or "")}
            if not entries or any(entries[-1][key] != entry[key] for key in ("status", "message")):
                entries.append(entry)
            dropped = max(0, len(entries) - LOG_LIMIT)
            job.update(updated_at=now, logs=entries[-LOG_LIMIT:], logs_omitted=previous.get("logs_omitted", 0) + dropped)
            self.service.store.put_state("job:" + job["id"], job)
            self.service.publish({"event": "job", "data": copy.deepcopy(job)})

    def submit(self, title, operation):
        identity = f"{time.time_ns()}-{uuid.uuid4().hex[:10]}"
        cancelled = threading.Event()
        job = {"id": identity, "title": title, "status": "queued", "message": "等待处理", "error": "", "result": None, "created_at": time.time()}
        self.save(job)
        log.info("job_submitted id=%s title=%s", identity, title)
        with self.lock:
            self.active[identity] = cancelled
        def progress(message, **fields):
            with self.lock:
                job.update(message=message, **fields)
                if cancelled.is_set() and job["status"] not in {"completed", "failed", "cancelled"}:
                    job["status"] = "stopping"
                self.save(job)
        def execute():
            try:
                if cancelled.is_set():
                    raise JobCancelled("已取消操作。")
                progress("正在处理…", status="running")
                log.info("job_started id=%s", identity)
                result = operation(cancelled, progress)
                progress("已完成", status="completed", result=result)
                log.info("job_completed id=%s", identity)
            except Exception as exc:
                log.exception("job_failed id=%s", identity)
                try:
                    progress(str(exc), status="cancelled" if isinstance(exc, JobCancelled) else "failed", error=str(exc))
                except Exception:
                    log.exception("job_status_save_failed id=%s", identity)
                    self.service.publish({"event": "service_error", "error": "操作结果无法保存，请查看诊断日志。", "job_id": identity})
            finally:
                with self.lock:
                    self.active.pop(identity, None)
        self.executor.submit(execute)
        return {"job_id": identity}

    def cancel(self, identity):
        with self.lock:
            event = self.active.get(identity)
            if event:
                job = self.get(identity)
                if job["status"] in {"queued", "running", "stopping"}:
                    event.set()
                    job.update(status="stopping", message="正在取消，已完成内容会保留…")
                    self.save(job)
                    log.info("job_cancel_requested id=%s", identity)
        return self.get(identity)

    def close(self):
        with self.lock:
            for cancelled in self.active.values():
                cancelled.set()
        self.executor.shutdown(wait=True, cancel_futures=False)

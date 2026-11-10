"""Observable, cancellable UI operations independent of renderer lifetime."""
from concurrent.futures import ThreadPoolExecutor
import copy
import logging
import threading
import time
import uuid

log = logging.getLogger("desktop.jobs")


class JobCancelled(RuntimeError):
    pass


class DesktopJobs:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="desktop-job")
        self.active = {}
        for job in self.list():
            if job["status"] in {"queued", "running", "stopping"}:
                job.update(status="interrupted", error="应用上次退出时此操作未完成。已有结果已保留，可重新操作。")
                self.save(job)

    def list(self):
        import json
        with self.service.storage._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM desktop_view_state WHERE key LIKE 'job:%' ORDER BY key DESC LIMIT 100")]

    def get(self, identity):
        job = self.service.store.get_state("job:" + identity)
        if not job:
            raise ValueError("操作记录不存在。")
        return job

    def save(self, job):
        job["updated_at"] = time.time()
        self.service.store.put_state("job:" + job["id"], job)
        self.service.publish({"event": "job", "data": copy.deepcopy(job)})

    def submit(self, title, operation):
        identity = f"{time.time_ns()}-{uuid.uuid4().hex[:10]}"
        cancelled = threading.Event()
        job = {"id": identity, "title": title, "status": "queued", "error": "", "result": None, "created_at": time.time()}
        self.save(job)
        with self.lock:
            self.active[identity] = cancelled
        def progress(message, **fields):
            with self.lock:
                job.update(message=message, **fields)
                self.save(job)
        def execute():
            try:
                if cancelled.is_set():
                    raise JobCancelled("已取消操作。")
                progress("正在处理…", status="running")
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
                event.set()
                job = self.get(identity)
                job.update(status="stopping", message="正在取消，已完成内容会保留…")
                self.save(job)
        return self.get(identity)

    def close(self):
        with self.lock:
            for cancelled in self.active.values():
                cancelled.set()
        self.executor.shutdown(wait=True, cancel_futures=False)

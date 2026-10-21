"""GUI-thread checks for existing worker ownership; no separate task registry."""

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


def ui_work_stopping(owner):
    return (getattr(owner, "_checkpoint_shutdown_pending", False) is True
            or getattr(owner, "_update_quiescing", False) is True)


def pause_ui_work_timers(owner):
    paused = []
    for name in ("daemon_timer", "favorites_timer", "deliverable_refresh_timer", "browser_file_refresh_timer"):
        timer = getattr(owner, name, None)
        # A coalesced refresh can still be pending after its timer fired while
        # the scanner was busy. Resume that request if shutdown is aborted.
        pending_refresh = (name == "deliverable_refresh_timer"
                           and getattr(owner, "deliverable_scan_pending", None) is not None)
        if timer is not None and (timer.isActive() or pending_refresh):
            timer.stop()
            paused.append(timer)
    return paused


def ui_worker_snapshot(owner):
    workers = list(owner.findChildren(QThread))
    app = QApplication.instance()
    if app is not None:
        workers.extend(app.findChildren(QThread))
        for widget in app.topLevelWidgets():
            workers.extend(widget.findChildren(QThread))
    workers.extend(getattr(owner, "_detached_workers", ()))
    workers.extend(getattr(owner, "_shutdown_wait_workers", ()))
    for state in getattr(owner, "sessions", {}).values():
        workers.extend((getattr(state, "llm_worker", None), getattr(state, "daemon_worker", None)))
        workers.extend(getattr(state, "guidance_workers", ()))
        for buffer in getattr(state, "ui_stream_buffers", ()):
            workers.append(getattr(buffer, "worker", None))
    # Qt's own native threads (for example QFileSystemModel's gatherer) stop
    # in their owner's destructor. Only Python worker subclasses belong here.
    return {worker for worker in workers
            if isinstance(worker, QThread) and type(worker) is not QThread and isValid(worker)}


def pending_ui_workers(owner, *, include_writers=False):
    excluded = () if include_writers else (
        getattr(owner, "chat_save_worker", None), getattr(owner, "runtime_checkpoint_worker", None),
    )
    # A native thread may have ended while its GUI completion is still queued
    # (or running a nested dialog). Its task slot must stay occupied until then.
    # deliverable_scan_worker is the occupied task slot. The reusable
    # deliverable_scanner is still found via Qt ownership, but is idle after wait(0).
    finishing = (getattr(owner, "daemon_connect_worker", None),
                 getattr(owner, "daemon_status_worker", None),
                 getattr(owner, "deliverable_scan_worker", None))
    return [worker for worker in ui_worker_snapshot(owner)
            if worker not in excluded and (worker in finishing or not worker.wait(0))]

"""Native task lifecycle and synchronous, ordered runtime notifications.

Notifications execute in the producing thread. UI consumers subscribe through
the desktop transport; no callback is implicitly dispatched to a GUI thread.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class EventSource:
    def __init__(self):
        self._lock = threading.RLock()
        self._observers: list[Callable] = []

    def subscribe(self, observer: Callable):
        if not callable(observer):
            raise TypeError("Event observer must be callable")
        with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)
        return lambda: self.unsubscribe(observer)

    def unsubscribe(self, observer: Callable):
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)

    @property
    def has_subscribers(self):
        with self._lock:
            return bool(self._observers)

    def emit(self, *args):
        with self._lock:
            observers = tuple(self._observers)
        failure = None
        for observer in observers:
            try:
                observer(*args)
            except Exception as exc:
                log.exception("runtime_observer_failed observer=%s", getattr(observer, "__name__", type(observer).__name__))
                if failure is None:
                    failure = exc
        # Deliver to every observer, then let the producer handle a failed
        # persistence/transport boundary. Never turn failed delivery into success.
        if failure is not None:
            raise failure


class TaskWorker:
    """One-shot task. Completion includes cleanup and lifecycle observers."""

    def __init__(self):
        self.finished = EventSource()
        self.failed = EventSource()
        self.failure: BaseException | None = None
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._finished = threading.Event()

    def start(self):
        with self._lifecycle_lock:
            if self._thread is not None:
                raise RuntimeError("A task can only be started once")
            self._thread = threading.Thread(target=self._execute, name=type(self).__name__, daemon=True)
            self._thread.start()

    def _execute(self):
        try:
            self.run()
        except BaseException as exc:
            self.failure = exc
            log.exception("runtime_task_failed worker=%s", type(self).__name__)
            try:
                self.failed.emit(exc)
            except Exception:
                log.exception("runtime_task_failure_delivery_failed worker=%s", type(self).__name__)
        finally:
            try:
                self.finished.emit()
            except Exception as exc:
                self.failure = self.failure or exc
                log.exception("runtime_task_cleanup_failed worker=%s", type(self).__name__)
            finally:
                self._finished.set()

    def run(self):
        raise NotImplementedError

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def is_finished(self):
        return self._finished.is_set()

    def is_current_thread(self):
        return threading.current_thread() is self._thread

    def join(self, timeout: float | None = None):
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return self._finished.is_set()
        thread.join(timeout)
        return not thread.is_alive()

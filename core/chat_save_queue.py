import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from .chat_storage import ChatStorage


@dataclass
class ChatSaveRequest:
    session_id: str
    messages: list
    title: str
    status: str
    meta: dict
    ready_at: float


class ChatSaveWorker(QThread):
    save_failed = Signal(str, str)
    save_completed = Signal(str)

    def __init__(self, db_path, debounce_ms=500, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.debounce_seconds = max(0, int(debounce_ms or 0)) / 1000.0
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._inflight = set()
        self._stop_requested = False

    def enqueue(self, request):
        if not isinstance(request, ChatSaveRequest):
            return
        request.ready_at = time.monotonic() + self.debounce_seconds
        with self._condition:
            self._pending[request.session_id] = request
            self._condition.notify_all()

    def flush(self, session_id=None, timeout_ms=3000):
        timeout_seconds = max(0, int(timeout_ms or 0)) / 1000.0
        deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
        target_session = str(session_id or "").strip() or None
        with self._condition:
            if target_session:
                request = self._pending.get(target_session)
                if request:
                    request.ready_at = time.monotonic()
            else:
                now = time.monotonic()
                for request in self._pending.values():
                    request.ready_at = now
            self._condition.notify_all()
            while True:
                if target_session:
                    idle = target_session not in self._pending and target_session not in self._inflight
                else:
                    idle = not self._pending and not self._inflight
                if idle:
                    return True
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(timeout=remaining)
                else:
                    self._condition.wait()

    def stop_worker(self, timeout_ms=3000):
        flushed = self.flush(timeout_ms=timeout_ms)
        with self._condition:
            self._stop_requested = True
            if not flushed:
                self._pending.clear()
            self._condition.notify_all()
        return self.wait(max(0, int(timeout_ms or 0)))

    def _next_request_locked(self):
        if self._pending:
            now = time.monotonic()
            next_session_id = None
            next_request = None
            next_ready_at = None
            for session_id, request in self._pending.items():
                if request.ready_at <= now:
                    next_session_id = session_id
                    next_request = request
                    break
                if next_ready_at is None or request.ready_at < next_ready_at:
                    next_ready_at = request.ready_at
            if next_request is not None:
                self._pending.pop(next_session_id, None)
                self._inflight.add(next_session_id)
                return next_request, 0.0
            if next_ready_at is not None:
                return None, max(0.0, next_ready_at - now)
        return None, None

    def run(self):
        storage = None
        while True:
            with self._condition:
                request, wait_time = self._next_request_locked()
                while request is None:
                    if self._stop_requested and not self._pending and not self._inflight:
                        return
                    self._condition.wait(timeout=wait_time)
                    request, wait_time = self._next_request_locked()

            try:
                if storage is None:
                    storage = ChatStorage(self.db_path)
                storage.save_conversation(
                    request.session_id,
                    request.messages,
                    title=request.title,
                    status=request.status,
                    meta=request.meta,
                )
            except Exception as exc:
                storage = None
                with self._condition:
                    self._inflight.discard(request.session_id)
                    current = self._pending.get(request.session_id)
                    if current is None or current.ready_at <= request.ready_at:
                        request.ready_at = time.monotonic() + self.debounce_seconds
                        self._pending[request.session_id] = request
                    self._condition.notify_all()
                self.save_failed.emit(request.session_id, str(exc))
                continue

            with self._condition:
                self._inflight.discard(request.session_id)
                self._condition.notify_all()
            self.save_completed.emit(request.session_id)

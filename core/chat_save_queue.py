import hashlib
import json
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
    revision: int = 0


class ChatSaveWorker(QThread):
    save_failed = Signal(str, int, str)
    save_completed = Signal(str, int)

    def __init__(self, db_path, debounce_ms=500, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.debounce_seconds = max(0, int(debounce_ms or 0)) / 1000.0
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._inflight = set()
        self._highest_revision = {}
        self._accepted_signature = {}
        self._stop_requested = False

    @staticmethod
    def _request_signature(request):
        payload = {
            "messages": request.messages,
            "title": request.title,
            "status": request.status,
            "meta": request.meta,
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            encoded = repr(payload)
        return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()

    def enqueue(self, request):
        if not isinstance(request, ChatSaveRequest):
            return False
        request.ready_at = time.monotonic() + self.debounce_seconds
        revision = max(0, int(request.revision or 0))
        signature = self._request_signature(request)
        with self._condition:
            highest_revision_value = self._highest_revision.get(request.session_id)
            highest_revision = (
                int(highest_revision_value)
                if highest_revision_value is not None
                else None
            )
            accepted_signature = self._accepted_signature.get(request.session_id)
            if highest_revision is not None and revision < highest_revision:
                # A stale UI callback is harmless, but it must never overwrite
                # a newer snapshot already accepted by this worker.
                return True
            if (
                highest_revision is not None
                and revision == highest_revision
                and accepted_signature
                and signature != accepted_signature
            ):
                return False
            if highest_revision is None or revision > highest_revision:
                self._highest_revision[request.session_id] = revision
                self._accepted_signature[request.session_id] = signature
            current = self._pending.get(request.session_id)
            if current is None or revision >= int(current.revision or 0):
                if current is not None and revision == int(current.revision or 0):
                    current_signature = self._request_signature(current)
                    if current_signature != signature:
                        return False
                self._pending[request.session_id] = request
            self._condition.notify_all()
        return True

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
                with self._condition:
                    newest_revision = int(self._highest_revision.get(request.session_id, 0) or 0)
                if int(request.revision or 0) < newest_revision:
                    # A newer request is already accepted and will be written
                    # by this single worker; do not let this stale request
                    # overwrite it if it was still in flight.
                    with self._condition:
                        self._inflight.discard(request.session_id)
                        self._condition.notify_all()
                    continue
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
                self.save_failed.emit(request.session_id, int(request.revision or 0), str(exc))
                continue

            with self._condition:
                self._inflight.discard(request.session_id)
                self._condition.notify_all()
            self.save_completed.emit(request.session_id, int(request.revision or 0))

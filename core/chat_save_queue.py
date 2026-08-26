import errno
import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from .chat_storage import ChatStorage, ConversationWriteConflict


SAVE_ERROR_BUSY = "busy"
SAVE_ERROR_NO_SPACE = "no_space"
SAVE_ERROR_PERMISSION = "permission"
SAVE_ERROR_PATH_UNAVAILABLE = "path_unavailable"
SAVE_ERROR_CONFLICT = "conflict"
SAVE_ERROR_CORRUPT = "corrupt"
SAVE_ERROR_UNKNOWN = "unknown"


def classify_chat_save_error(exc):
    """Return a stable persistence error category from typed error metadata."""

    if isinstance(exc, ConversationWriteConflict):
        return SAVE_ERROR_CONFLICT

    sqlite_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(sqlite_code, int):
        primary_code = sqlite_code & 0xFF
        if primary_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return SAVE_ERROR_BUSY
        if primary_code == sqlite3.SQLITE_FULL:
            return SAVE_ERROR_NO_SPACE
        if primary_code == sqlite3.SQLITE_READONLY:
            return SAVE_ERROR_PERMISSION
        if primary_code == sqlite3.SQLITE_CANTOPEN:
            return SAVE_ERROR_PATH_UNAVAILABLE
        if primary_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            return SAVE_ERROR_CORRUPT

    os_errno = getattr(exc, "errno", None)
    if os_errno in {errno.EBUSY, getattr(errno, "ETXTBSY", -1)}:
        return SAVE_ERROR_BUSY
    if os_errno == errno.ENOSPC:
        return SAVE_ERROR_NO_SPACE
    if os_errno in {errno.EACCES, errno.EPERM, getattr(errno, "EROFS", -1)}:
        return SAVE_ERROR_PERMISSION
    if os_errno in {errno.ENOENT, errno.ENOTDIR}:
        return SAVE_ERROR_PATH_UNAVAILABLE

    winerror = getattr(exc, "winerror", None)
    if winerror in {32, 33}:
        return SAVE_ERROR_BUSY
    if winerror == 112:
        return SAVE_ERROR_NO_SPACE
    if winerror == 5:
        return SAVE_ERROR_PERMISSION
    if winerror in {2, 3, 53, 67}:
        return SAVE_ERROR_PATH_UNAVAILABLE
    return SAVE_ERROR_UNKNOWN


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
    save_failed = Signal(str, int, str, str)
    save_blocked = Signal(str, int, str)
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
        self._completed_revision = {}
        self._blocked_revision = {}
        self._failure_counts = {}
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

    def _retry_delay_seconds(self, failure_count):
        base_delay = max(0.5, self.debounce_seconds)
        exponent = min(max(0, int(failure_count or 0) - 1), 6)
        return min(30.0, base_delay * (2 ** exponent))

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

    def wait_for_revision(self, session_id, revision, timeout_ms=3000):
        """Wait until a specific session snapshot is durably committed.

        Guidance delivery uses this as a local transaction barrier: the UI
        ledger must contain the visible assistant stage and guidance before a
        daemon is allowed to apply that guidance to the active model run.
        """
        target_session = str(session_id or "").strip()
        target_revision = max(0, int(revision or 0))
        if not target_session or target_revision <= 0:
            return False
        timeout_seconds = max(0, int(timeout_ms or 0)) / 1000.0
        deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
        with self._condition:
            request = self._pending.get(target_session)
            if request and int(request.revision or 0) >= target_revision:
                request.ready_at = time.monotonic()
            self._condition.notify_all()
            while True:
                completed = int(self._completed_revision.get(target_session, 0) or 0)
                if completed >= target_revision:
                    return True
                blocked = int(self._blocked_revision.get(target_session, 0) or 0)
                if blocked >= target_revision:
                    return False
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
                storage.save_conversation_safely(
                    request.session_id,
                    request.messages,
                    title=request.title,
                    status=request.status,
                    meta=request.meta,
                )
            except ConversationWriteConflict as exc:
                storage = None
                with self._condition:
                    self._failure_counts.pop(request.session_id, None)
                    self._blocked_revision[request.session_id] = max(
                        int(self._blocked_revision.get(request.session_id, 0) or 0),
                        int(request.revision or 0),
                    )
                    self._inflight.discard(request.session_id)
                    self._condition.notify_all()
                self.save_blocked.emit(
                    request.session_id,
                    int(request.revision or 0),
                    str(exc),
                )
                continue
            except Exception as exc:
                storage = None
                with self._condition:
                    self._inflight.discard(request.session_id)
                    current = self._pending.get(request.session_id)
                    if current is None or current.ready_at <= request.ready_at:
                        failure_count = int(self._failure_counts.get(request.session_id, 0) or 0) + 1
                        self._failure_counts[request.session_id] = failure_count
                        retry_delay = self._retry_delay_seconds(failure_count)
                        request.ready_at = time.monotonic() + retry_delay
                        self._pending[request.session_id] = request
                    self._condition.notify_all()
                self.save_failed.emit(
                    request.session_id,
                    int(request.revision or 0),
                    classify_chat_save_error(exc),
                    str(exc),
                )
                continue

            with self._condition:
                self._failure_counts.pop(request.session_id, None)
                self._completed_revision[request.session_id] = max(
                    int(self._completed_revision.get(request.session_id, 0) or 0),
                    int(request.revision or 0),
                )
                self._inflight.discard(request.session_id)
                self._condition.notify_all()
            self.save_completed.emit(request.session_id, int(request.revision or 0))

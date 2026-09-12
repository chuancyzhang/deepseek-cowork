"""Coalesce worker notifications before they enter the GUI event queue."""

import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot


@dataclass
class _PendingEvent:
    kind: str
    args: tuple
    parts: list = field(default_factory=list)
    characters: int = 0
    received: int = 1
    connected: dict | None = None


class StreamEventBuffer(QObject):
    """One worker's ordered notifications, owned and consumed by the GUI.

    Direct signal connections only call enqueue(), which touches Python data
    under a lock. A single queued wakeup starts the GUI timer; no QWidget or
    session state is accessed from the producer thread. Semantic events seal
    text segments, including result delivery and the native thread finish.
    """

    wakeup = Signal()
    INTERVAL_MS = 16
    BUDGET_SECONDS = 0.008
    MAX_TEXT_PARTS = 256
    MAX_TEXT_CHARACTERS = 65536

    def __init__(self, parent, *, report):
        super().__init__(parent)
        self._report = report
        self._handlers = {}
        self._connections = []
        self._text_kinds = set()
        self._lock = threading.Lock()
        self._queue = deque()
        self._scheduled = False
        self._closed = False
        self._discard_terminal = False
        self._cursor_failed = False
        self._draining = False
        self._transport_state = None
        self._received = 0
        self._applied = 0
        self._rejected = 0
        self._discarded_terminal = 0
        self._failed_events = 0
        self._text_parts = 0
        self._text_batches = 0
        self._connected_merged = 0
        self._batches = 0
        self._max_batch_ms = 0.0
        self._last_report = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._drain)
        self.wakeup.connect(self._wake, Qt.QueuedConnection)

    def connect_signal(self, signal, kind, handler, *, text=False):
        """Configure on the GUI thread before starting the worker."""
        if kind in self._handlers:
            raise ValueError(f"Duplicate stream event handler: {kind}")
        self._handlers[kind] = handler
        if text:
            self._text_kinds.add(kind)
        receive = lambda *args, name=kind: self.enqueue(name, args)
        signal.connect(receive, Qt.DirectConnection)
        self._connections.append((signal, receive))

    def enqueue(self, kind, args):
        """Producer-thread entry point. Never starts a timer or calls a handler."""
        with self._lock:
            if self._closed:
                return
            self._received += 1
            tail = self._queue[-1] if self._queue else None
            connected = None
            if kind == "stream_state":
                data = dict(args[0])
                status = data.get("status")
                # Keep transitions as barriers. Only redundant connected
                # notifications can travel with the preceding applied data.
                if status == self._transport_state == "connected":
                    connected = data
                self._transport_state = status
                args = (data,)
            if connected is not None and tail is not None:
                tail.connected = connected
                tail.received += 1
                self._connected_merged += 1
            elif kind in self._text_kinds:
                text = str(args[0] or "")
                if (
                    tail is not None and tail.kind == kind
                    and len(tail.parts) < self.MAX_TEXT_PARTS
                    and tail.characters + len(text) <= self.MAX_TEXT_CHARACTERS
                ):
                    tail.parts.append(text)
                    tail.characters += len(text)
                    tail.received += 1
                else:
                    self._queue.append(_PendingEvent(kind, (), [text], len(text)))
                self._text_parts += 1
            else:
                self._queue.append(_PendingEvent(kind, args))
            if not self._scheduled:
                self._scheduled = True
                # A queued connection cannot call back while this lock is held.
                # Emitting here also closes the race with GUI-side retirement.
                self.wakeup.emit()

    @Slot()
    def _wake(self):
        with self._lock:
            pending = bool(self._queue) and not self._closed
        if pending and not self._draining and not self._timer.isActive():
            self._timer.start(self.INTERVAL_MS)

    def _apply(self, event):
        if self._discard_terminal and event.kind in {"result", "native_finished"}:
            self._discarded_terminal += event.received
            return
        handler = self._handlers[event.kind]
        if event.parts:
            delivered = handler("".join(event.parts), event.parts)
            self._text_batches += 1
        elif event.kind == "stream_state":
            delivered = self._apply_stream_state(event.args[0])
        else:
            delivered = handler(*event.args)
        if delivered is False:
            self._rejected += event.received
            return
        if event.connected is not None:
            # Never acknowledge a daemon cursor before the associated data.
            self._apply_stream_state(event.connected)
        self._applied += event.received

    def _apply_stream_state(self, data):
        if self._cursor_failed and data.get("status") == "connected":
            data = {**data, "sequence": 0}
        return self._handlers["stream_state"](data)

    def _resume(self):
        with self._lock:
            pending = bool(self._queue) and not self._closed
            self._scheduled = pending
        if pending and not self._timer.isActive():
            self._timer.start(self.INTERVAL_MS)

    def _consume(self, take_next, *, budget=None):
        if self._draining:
            return
        self._draining = True
        started = time.monotonic()
        current = None
        try:
            while True:
                current = take_next()
                if current is None:
                    break
                try:
                    self._apply(current)
                except Exception:
                    # Match independent Qt deliveries: surface the exception,
                    # retain later events, and never replay a partially applied
                    # event. A failure also fences off subsequent cursor acks.
                    self._cursor_failed = True
                    self._failed_events += current.received
                    self._report(
                        "error", event_kind=current.kind,
                        traceback=traceback.format_exc(),
                    )
                current = None
                if budget is not None and time.monotonic() - started >= budget:
                    break
        finally:
            self._draining = False
            self._batches += 1
            self._max_batch_ms = max(self._max_batch_ms, (time.monotonic() - started) * 1000)
            self._resume()
            self.report(force=self._closed)
            if self._closed:
                self._handlers.clear()

    @Slot()
    def _drain(self):
        def take_next():
            with self._lock:
                return self._queue.popleft() if self._queue and not self._closed else None

        self._consume(take_next, budget=self.BUDGET_SECONDS)

    def flush(self):
        """Apply a finite snapshot before a GUI-initiated semantic boundary.

        New producer events enter a separate queue, so continuous output cannot
        extend this synchronous boundary indefinitely. Nested calls from an
        event handler are already ordered and must not consume later events.
        """
        if self._draining:
            return
        self._timer.stop()
        with self._lock:
            pending, self._queue = self._queue, deque()
        try:
            self._consume(lambda: pending.popleft() if pending else None)
        finally:
            if pending:
                with self._lock:
                    pending.extend(self._queue)
                    self._queue = pending
                self._resume()

    def close(self, *, discard_terminal=False):
        """Seal input, preserve already received events, then retire the timer."""
        with self._lock:
            self._closed = True
        # User cancellation wins over a result that has not yet reached the UI.
        self._discard_terminal = discard_terminal
        self._timer.stop()
        for signal, receive in self._connections:
            try:
                signal.disconnect(receive)
            except RuntimeError:
                # The worker may already have been deleted by its owner.
                pass
        self._connections.clear()
        self.flush()
        self.report(force=True)

    def report(self, *, force=False):
        now = time.monotonic()
        if not force and now - self._last_report < 1.0:
            return
        self._last_report = now
        with self._lock:
            counts = {
                "received_events": self._received,
                "pending_events": sum(event.received for event in self._queue),
                "pending_characters": sum(event.characters for event in self._queue),
                "text_chunks": self._text_parts,
                "connected_updates_merged": self._connected_merged,
            }
        self._report(
            "summary", **counts, applied_events=self._applied,
            rejected_events=self._rejected, discarded_terminal_events=self._discarded_terminal,
            failed_events=self._failed_events,
            text_batches=self._text_batches, batches=self._batches,
            max_batch_ms=round(self._max_batch_ms, 3),
        )

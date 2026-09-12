"""Ordered, bounded stream persistence with an independent socket subscriber."""

import copy
import json
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class _Entry:
    payload: dict
    size: int
    created_at: float
    queued_at: float
    ticket: int
    urgent: bool


class RuntimeStream:
    INTERVAL = 0.016
    BATCH_EVENTS = 64
    BATCH_BYTES = 256 * 1024
    MAX_EVENTS = 4096
    MAX_BYTES = 8 * 1024 * 1024
    BOUNDARIES = frozenset({
        "turn_started", "content_snapshot", "tool_call", "tool_result",
        "interaction_request", "final",
    })

    def __init__(self, journal, session_id, run_id, *, write, detach, failed, report):
        self.journal = journal
        self.session_id = session_id
        self.run_id = run_id
        self._write = write
        self._detach = detach
        self._failed = failed
        self._report = report
        self._condition = threading.Condition()
        self._pending = deque()
        self._sending = deque()
        self._pending_bytes = 0
        self._sending_bytes = 0
        self._accepted = 0
        self._committed = 0
        self._flush_ticket = 0
        self._stopped = False
        self._closing = False
        self._writer_done = False
        self._closed = False
        self._subscriber_closed = False
        self.error = None
        self.last_sequence = 0
        self._metrics = {}
        self._last_report = time.monotonic()
        self._writer = threading.Thread(target=self._persist, name=f"journal-{run_id}", daemon=True)
        self._sender = threading.Thread(target=self._send, name=f"stream-{run_id}", daemon=True)
        self._writer.start()
        self._sender.start()

    @staticmethod
    def _full(queue, used, size):
        # One oversized record can occupy an otherwise empty queue.
        return bool(queue) and (len(queue) >= RuntimeStream.MAX_EVENTS or used + size > RuntimeStream.MAX_BYTES)

    def enqueue(self, payload, *, terminal=False):
        created_at, queued_at = time.time(), time.monotonic()
        payload = copy.deepcopy(payload)
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        urgent = payload.get("type") in self.BOUNDARIES
        with self._condition:
            while self._full(self._pending, self._pending_bytes, size):
                if self.error or self._closing or (self._stopped and not terminal):
                    break
                self._condition.wait()
            if self.error:
                raise RuntimeError("Runtime stream persistence failed") from self.error
            if self._closing or (self._stopped and not terminal):
                return False
            self._accepted += 1
            ticket = self._accepted
            self._pending.append(_Entry(payload, size, created_at, queued_at, ticket, urgent))
            self._pending_bytes += size
            if urgent:
                self._flush_ticket = ticket
            self._condition.notify_all()
        if urgent:
            self.flush(ticket)
        return True

    def stop_accepting(self):
        with self._condition:
            self._stopped = True
            self._flush_ticket = self._accepted
            self._condition.notify_all()

    @property
    def active(self):
        with self._condition:
            return not self._closed

    @property
    def drained(self):
        with self._condition:
            return self.error is not None or self._accepted == self._committed

    def flush(self, ticket=None):
        with self._condition:
            ticket = self._accepted if ticket is None else ticket
            self._flush_ticket = max(self._flush_ticket, ticket)
            self._condition.notify_all()
            while self._committed < ticket and self.error is None:
                self._condition.wait()
            if self.error:
                raise RuntimeError("Runtime stream persistence failed") from self.error

    def disconnect(self, reason):
        with self._condition:
            if self._subscriber_closed:
                return
            self._subscriber_closed = True
            self._sending.clear()
            self._sending_bytes = 0
            self._condition.notify_all()
        # The callback only closes the connection and reports the reason. It
        # must never append to the journal or acquire its session lock.
        self._detach(reason)

    def _record(self, *, force=False, **values):
        with self._condition:
            for name, value in values.items():
                self._metrics[name] = self._metrics.get(name, 0) + value
            now = time.monotonic()
            if not force and now - self._last_report < 1:
                return
            metrics = self._metrics
            self._metrics = {}
            self._last_report = now
            metrics.update(pending_events=len(self._pending), pending_bytes=self._pending_bytes,
                           sending_events=len(self._sending), last_sequence=self.last_sequence)
        self._report(**metrics)

    def _persist(self):
        try:
            while True:
                with self._condition:
                    while not self._pending:
                        if self._closing:
                            return
                        self._condition.wait()
                    deadline = self._pending[0].queued_at + self.INTERVAL
                    while (not self._closing and self._flush_ticket <= self._committed
                           and len(self._pending) < self.BATCH_EVENTS
                           and self._pending_bytes < self.BATCH_BYTES):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
                    batch = []
                    size = 0
                    while self._pending and len(batch) < self.BATCH_EVENTS:
                        entry = self._pending[0]
                        if batch and size + entry.size > self.BATCH_BYTES:
                            break
                        batch.append(self._pending.popleft())
                        size += entry.size
                        if entry.urgent:
                            break
                    self._pending_bytes -= size
                    self._condition.notify_all()
                started = time.monotonic()
                events = self.journal.append_events(self.session_id, self.run_id, [
                    {"type": entry.payload.get("type") or "stream_event",
                     "payload": entry.payload, "created_at": entry.created_at}
                    for entry in batch
                ])
                overflow = False
                with self._condition:
                    self.last_sequence = int(events[-1]["sequence"])
                    self._committed = batch[-1].ticket
                    if not self._subscriber_closed:
                        for entry, event in zip(batch, events):
                            if self._full(self._sending, self._sending_bytes, entry.size):
                                overflow = True
                                break
                            self._sending.append((entry.payload, event["sequence"], entry.size))
                            self._sending_bytes += entry.size
                    self._condition.notify_all()
                if overflow:
                    self.disconnect("subscriber_backlog_exceeded")
                self._record(batches=1, batch_bytes=size,
                             queue_wait_ms=(started - batch[0].queued_at) * 1000,
                             **self.journal.append_metrics())
        except Exception as exc:
            with self._condition:
                self.error = exc
                self._stopped = True
                # Keep the uncommitted data for diagnosis; never replay an
                # uncertain partial write automatically.
                self._failed_batch = locals().get("batch", [])
                self._condition.notify_all()
            self._record(force=True, failures=1)
            self._failed(exc)
        finally:
            with self._condition:
                self._writer_done = True
                self._condition.notify_all()

    def _send(self):
        try:
            while True:
                with self._condition:
                    while not self._sending:
                        if self._writer_done or self._subscriber_closed:
                            return
                        self._condition.wait()
                    payload, sequence, size = self._sending.popleft()
                    self._sending_bytes -= size
                started = time.monotonic()
                if not self._write(payload, sequence):
                    self.disconnect("socket_write_failed")
                    return
                self._record(send_ms=(time.monotonic() - started) * 1000, sent_events=1)
        except Exception as exc:
            self.disconnect(f"socket_write_failed:{type(exc).__name__}")

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closing = True
            self._condition.notify_all()
        self._writer.join()
        self._sender.join(5)
        if self._sender.is_alive():
            self.disconnect("subscriber_close_timeout")
            self._sender.join(5)
        self._record(force=True)
        with self._condition:
            self._closed = True

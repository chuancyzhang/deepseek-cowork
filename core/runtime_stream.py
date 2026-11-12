"""Ordered, bounded stream persistence with an independent socket subscriber."""

import copy
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from contextlib import ExitStack


class _StreamDispatcher:
    """Fair batching for every stream owned by one journal."""
    _creation = threading.Lock()

    @classmethod
    def attach(cls, stream):
        with cls._creation:
            dispatcher = getattr(stream.journal, "_stream_dispatcher", None)
            if dispatcher is None or dispatcher.finished:
                dispatcher = cls(stream.journal)
                stream.journal._stream_dispatcher = dispatcher
            with dispatcher.condition:
                dispatcher.streams.append(stream)
                dispatcher.condition.notify_all()
            return dispatcher

    def __init__(self, journal):
        self.journal = journal
        self.condition = threading.Condition()
        self.streams = deque()
        self.finished = False
        self.thread = threading.Thread(target=self.run, name="runtime-streams", daemon=True)
        self.thread.start()

    def wake(self):
        with self.condition:
            self.condition.notify_all()

    def run(self):
        while True:
            with self.condition:
                self.condition.wait(.016)
                streams = list(self.streams)
                self.streams.rotate(-1)
            selected = []
            remaining = RuntimeStream.BATCH_EVENTS
            remaining_bytes = RuntimeStream.BATCH_BYTES
            for stream in streams:
                with stream._condition:
                    if stream.error or (stream._closing and not stream._pending):
                        stream._writer_done = True
                        stream._condition.notify_all()
                        with self.condition:
                            if stream in self.streams:
                                self.streams.remove(stream)
                        continue
                    if not stream._pending or remaining <= 0 or remaining_bytes <= 0:
                        continue
                    batch, size = [], 0
                    while stream._pending and len(batch) < remaining:
                        entry = stream._pending[0]
                        if (batch or selected) and size + entry.size > remaining_bytes:
                            break
                        batch.append(stream._pending.popleft())
                        size += entry.size
                        if entry.urgent:
                            break
                    if not batch:
                        continue
                    stream._pending_bytes -= size
                    stream._condition.notify_all()
                    selected.append((stream, batch, size))
                    remaining -= len(batch)
                    remaining_bytes -= size
            if selected:
                started = time.monotonic()
                try:
                    committed = []
                    with ExitStack() as locks:
                        # The ledger groups these changes into one durable record.
                        # Legacy journals keep their distributed on-disk format.
                        if hasattr(self.journal, "ledger"):
                            locks.enter_context(self.journal.session_locks(sorted({stream.session_id for stream, _, _ in selected})))
                        for stream, batch, size in selected:
                            events = self.journal.append_events(stream.session_id, stream.run_id, [
                                {"type": entry.payload.get("type") or "stream_event", "payload": entry.payload,
                                 "created_at": entry.created_at} for entry in batch])
                            committed.append((stream, batch, size, events))
                    for stream, batch, size, events in committed:
                        stream._persisted(batch, size, events, started)
                except Exception as error:
                    for stream, batch, _ in selected:
                        stream._persistence_failed(error, batch)
            with self._creation:
                with self.condition:
                    if not self.streams:
                        self.finished = True
                        return


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
        "interaction_request", "interaction_finished", "workbench_request", "final",
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
        self._sender = threading.Thread(target=self._send, name=f"stream-{run_id}", daemon=True)
        self._dispatcher = _StreamDispatcher.attach(self)
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
        self._dispatcher.wake()
        if urgent:
            self.flush(ticket)
        return True

    def stop_accepting(self):
        with self._condition:
            self._stopped = True
            self._flush_ticket = self._accepted
            self._condition.notify_all()
        self._dispatcher.wake()

    @property
    def active(self):
        with self._condition:
            return not self._closed

    @property
    def drained(self):
        with self._condition:
            return self.error is not None or self._accepted == self._committed

    def flush(self, ticket=None):
        self._dispatcher.wake()
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

    def _persisted(self, batch, size, events, started):
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
        self._record(batches=1, batch_bytes=size, queue_wait_ms=(started-batch[0].queued_at)*1000,
                     **self.journal.append_metrics())

    def _persistence_failed(self, error, batch):
        with self._condition:
            self.error = error
            self._stopped = True
            self._failed_batch = batch
            self._writer_done = True
            self._condition.notify_all()
        self._record(force=True, failures=1)
        self._failed(error)

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
        self._dispatcher.wake()
        with self._condition:
            while not self._writer_done:
                self._condition.wait()
        self._sender.join(5)
        if self._sender.is_alive():
            self.disconnect("subscriber_close_timeout")
            self._sender.join(5)
        self._record(force=True)
        with self._condition:
            self._closed = True

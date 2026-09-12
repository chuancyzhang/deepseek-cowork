"""Coalesced recovery snapshots. Only immutable data crosses the GUI boundary."""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal


@dataclass(frozen=True)
class CheckpointRequest:
    session_id: str
    run_id: str
    instance_id: str
    revision: int
    content: str
    reasoning: str
    boundary: str = ""

    @property
    def key(self):
        return self.session_id, self.run_id, self.instance_id


class RuntimeCheckpointWorker(QThread):
    completed = Signal(object, str, str)
    measured = Signal(object)

    def __init__(self, journal, parent=None):
        super().__init__(parent)
        self.journal = journal
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._sealed = set()
        self._instances = {}
        self._revisions = {}
        self._failures = {}
        self._stop_requested = False
        self._merged = {}

    def enqueue(self, request):
        with self._condition:
            if self._stop_requested or (request.key in self._sealed and not request.boundary):
                return False
            identity = request.session_id, request.run_id
            self._instances[identity] = request.instance_id
            if request.revision <= self._revisions.get(request.key, 0):
                return False
            self._revisions[request.key] = request.revision
            if request.boundary:
                self._sealed.add(request.key)
            key = (*request.key, request.revision if request.boundary else "periodic")
            if key in self._pending:
                self._merged[request.key] = self._merged.get(request.key, 0) + 1
            self._pending[key] = request
            self._condition.notify_all()
            return True

    def request_stop(self):
        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()

    def run(self):
        while True:
            with self._condition:
                while not self._pending:
                    if self._stop_requested:
                        return
                    self._condition.wait()
                _, request = self._pending.popitem(last=False)
                stale = self._instances.get((request.session_id, request.run_id)) != request.instance_id
                merged = self._merged.pop(request.key, 0)
            started = time.monotonic()
            outcome, error = "expired", ""
            try:
                if not stale and request.boundary in {"stop", "exit_local"}:
                    reason = "interrupted by user" if request.boundary == "stop" else "application_exit"
                    self.journal.interrupt_run(
                        request.session_id, request.run_id, reason=reason,
                        patch={"draft_content": request.content, "draft_reasoning": request.reasoning},
                    )
                    self.journal.append_event(
                        request.session_id, request.run_id, "interrupted", {"reason": reason},
                    )
                    outcome = "saved"
                elif not stale:
                    outcome = self.journal.write_checkpoint(
                        request.session_id, request.run_id,
                        content=request.content, reasoning=request.reasoning,
                        instance_id=request.instance_id, revision=request.revision,
                    )
                if outcome == "saved":
                    with self._condition:
                        self._failures.pop(request.key, None)
            except Exception as exc:
                outcome, error = "failed", str(exc)
                with self._condition:
                    # Keep the failed snapshot and reason. No implicit retry of
                    # a potentially partial journal append.
                    self._failures[request.key] = (request, error)
                    self._sealed.add(request.key)
            self.measured.emit({
                "session_id": request.session_id, "run_id": request.run_id,
                "revision": request.revision, "boundary": request.boundary,
                "save_ms": round((time.monotonic() - started) * 1000, 3),
                "merged": merged, "outcome": outcome,
            })
            self.completed.emit(request, outcome, error)

"""One cancellable sidebar query at a time; publish only the latest request."""

import time

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt

from core.sidebar_search import SearchCancelled


class HistorySearchWorker(QThread):
    def __init__(self, storage, query, projects, generation, parent=None):
        super().__init__(parent)
        self.storage, self.query, self.projects = storage, query, projects
        self.generation = generation
        self.result = None

    def run(self):
        started = time.perf_counter()
        result = {"query": self.query, "generation": self.generation, "ok": False}
        try:
            conversations, ids = self.storage.search_sidebar_conversations(
                self.query, self.projects, cancel_check=self.isInterruptionRequested,
            )
            result.update(ok=True, ids=ids, conversations=conversations)
        except Exception as exc:
            result.update(cancelled=self.isInterruptionRequested() or isinstance(exc, SearchCancelled),
                          error_type=type(exc).__name__, sqlite_errorcode=getattr(exc, "sqlite_errorcode", None))
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        self.result = result


class HistorySearchController(QObject):
    completed = Signal(dict)

    def __init__(self, parent=None, *, log_event=None, can_start=None):
        super().__init__(parent)
        self.generation = 0
        self.worker = None
        self.pending = None
        self.log_event = log_event or (lambda *_args, **_kwargs: None)
        self.can_start = can_start or (lambda: True)

    def cancel(self):
        self.generation += 1
        self.pending = None
        if self.worker is not None:
            self.worker.requestInterruption()

    def submit(self, storage, query, projects=()):
        self.cancel()
        self.pending = (storage, query, [dict(project) for project in projects], self.generation)
        self._start_pending()

    def _start_pending(self):
        if self.worker is not None or self.pending is None:
            return
        if not self.can_start():
            self.pending = None
            return
        storage, query, projects, generation = self.pending
        self.pending = None
        self.worker = HistorySearchWorker(storage, query, projects, generation, self)
        self.worker.finished.connect(self._finished, Qt.QueuedConnection)
        self.log_event("history_search_start", generation=generation, query_length=len(query))
        self.worker.start()

    @Slot()
    def _finished(self):
        worker = self.worker
        result = worker.result or {"ok": False, "query": worker.query,
                                   "generation": worker.generation, "error_type": "WorkerEnded"}
        self.worker = None
        worker.deleteLater()
        current = result["generation"] == self.generation
        event = "cancelled" if not current or result.get("cancelled") else "done" if result["ok"] else "failed"
        self.log_event("history_search_" + event, generation=result["generation"],
                       elapsed_ms=result.get("elapsed_ms", 0),
                       matches=len(result.get("conversations", ())), error_type=result.get("error_type", ""),
                       sqlite_errorcode=result.get("sqlite_errorcode"))
        if current and not result.get("cancelled") and self.can_start():
            self.completed.emit(result)
        self._start_pending()

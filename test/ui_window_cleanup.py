"""Drain the real asynchronous shutdown before destroying test windows."""
import time

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


def close_test_window(window, timeout=10, *, delete=True):
    finished = []
    window._shutdown_workers_async(lambda _preserved: finished.append(True))
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    while not finished and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    if not finished:
        raise AssertionError("Test window did not finish saving and stopping its workers")
    window._checkpoint_close_ready = True
    window.close()
    # Layout/scroll callbacks can post another zero-delay callback while closing.
    for _ in range(3):
        app.processEvents()
    if delete:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

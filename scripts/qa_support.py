"""Shared temporary paths and real-window teardown for standalone QA."""

import os
from pathlib import Path
import tempfile
import time


def prepare_qa_directory(script: str) -> Path:
    source = Path(script).resolve()
    base = Path(os.environ.get("COWORK_QA_ROOT") or source.parent.parent / ".tmp").resolve()
    root = base / source.stem
    temporary = root / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(temporary)
    os.environ["TMP"] = str(temporary)
    tempfile.tempdir = str(temporary)
    return root


def close_main_window(window, timeout=5):
    """Await the application's asynchronous shutdown before deleting Qt objects."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    window.close()
    deadline = time.monotonic() + timeout
    while not getattr(window, "_checkpoint_close_ready", False):
        if time.monotonic() >= deadline:
            raise AssertionError("MainWindow shutdown did not finish; window was not deleted")
        app.processEvents()
        time.sleep(0.01)
    window.deleteLater()
    QApplication.sendPostedEvents(window, QEvent.DeferredDelete)

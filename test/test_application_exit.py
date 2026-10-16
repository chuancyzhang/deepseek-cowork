"""Exercise QApplication.quit through the real window close event in isolation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def run_exit_scenario(scenario):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PySide6.QtCore import QEvent, QThread, QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow
    import main
    from ui.worker_lifecycle import pending_ui_workers

    class PendingOperation(QThread):
        def request_stop(self):
            pass  # Model the brief delay between stop request and native return.

        def run(self):
            release.wait(5)

    app = QApplication([])
    main.initialize_desktop_theme(app)
    effects, errors = [], []
    app.aboutToQuit.connect(lambda: effects.append("application_quit"))
    release = threading.Event()
    with patch.object(main.MainWindow, "showEvent", lambda w, e: QMainWindow.showEvent(w, e)), \
         patch.object(main.MainWindow, "_show_model_onboarding_if_needed", lambda w: None), \
         patch("sys.excepthook", lambda kind, value, trace: errors.append(str(value))):
        window = main.MainWindow()
        window.daemon_client = SimpleNamespace(shutdown=lambda: effects.append("daemon_shutdown"))
        window.daemon_available = True
        window.stop_daemon_process = lambda: effects.append("daemon_stop")
        window.stop_gateway_process = lambda: effects.append("gateway_stop")
        window.tray_icon = None if scenario == "window" else SimpleNamespace(
            hide=lambda: effects.append("tray_hide"), showMessage=lambda *args: None)
        if scenario == "preserve":
            window.get_current_session().daemon_running = True
        pending = PendingOperation(window)
        if scenario == "update":
            window.runtime_checkpoint_worker.request_stop()
            assert window.runtime_checkpoint_worker.wait(3000)
            window.runtime_checkpoint_worker = pending
        pending.start()
        window.show()

        def begin():
            if scenario == "update":
                # Installation committed, but a writer is still returning.
                window._update_preparing = window._update_exit_committed = True
                window.chat_save_worker.request_stop()
                window.runtime_checkpoint_worker.request_stop()
                window._finish_update_exit()
            elif scenario == "window":
                window.close()
            else:
                # The window's X still minimizes to tray before explicit exit.
                assert not window.close()
                assert not window.isVisible()
                assert not getattr(window, "_checkpoint_shutdown_pending", False)
                window.show()
                window.quit_app()
                window.quit_app()
            if scenario != "update":
                assert window._checkpoint_shutdown_pending
            assert not getattr(window, "_checkpoint_close_ready", False)
            assert not window.close(), "Closing must stay blocked while a worker is running"
            assert window.isVisible()
            release.set()

        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda: app.exit(91))
        watchdog.start(4000)
        QTimer.singleShot(0, begin)
        try:
            exit_code = app.exec()
            assert exit_code == 0, ("Qt exit timed out", effects, errors)
            assert not errors, errors
            assert effects.count("application_quit") == 1, effects
            assert not window.isVisible(), "The main window survived application exit"
            assert not pending_ui_workers(window, include_writers=True)
            assert effects.count("gateway_stop") == (0 if scenario == "update" else 1), effects
            if scenario == "preserve":
                assert "daemon_shutdown" not in effects and "daemon_stop" not in effects, effects
            elif scenario == "tray":
                assert effects.count("daemon_shutdown") == effects.count("daemon_stop") == 1, effects
            if scenario != "window":
                assert effects.count("tray_hide") == 1, effects
        finally:
            watchdog.stop()
            release.set()
            for worker in window.findChildren(QThread):
                if hasattr(worker, "request_stop"):
                    worker.request_stop()
                assert worker.wait(3000)
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)


class ApplicationExitTests(unittest.TestCase):
    def test_real_application_exit(self):
        for scenario in ("tray", "preserve", "window", "update"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="cowork-exit-test-") as root:
                data = Path(root) / "appdata" / "DeepSeekCowork"
                data.mkdir(parents=True)
                # Prevent migration from the checkout's real config.json.
                (data / "config.json").write_text("{}", encoding="utf-8")
                environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                    COWORK_EXTERNAL_BACKGROUND_SERVICES="0", PYTHONUTF8="1",
                    APPDATA=str(Path(root) / "appdata"), LOCALAPPDATA=str(Path(root) / "localappdata"))
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), scenario],
                    cwd=Path(__file__).resolve().parents[1], env=environment,
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in {"tray", "preserve", "window", "update"}:
        run_exit_scenario(sys.argv[1])
    else:
        unittest.main()

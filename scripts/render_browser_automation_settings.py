"""Light-theme browser-mode settings QA in isolated application storage."""
from pathlib import Path
import argparse
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / ".tmp/browser-mode-qa"))
    options = parser.parse_args()
    output = Path(options.output); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-browser-mode-") as temporary:
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        config_dir = Path(os.environ["APPDATA"]) / "DeepSeekCowork"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtGui import QFontDatabase
        from core.browser_backend import bind_run, finish_run, browser_operation, EXTERNAL
        from core.execution_authorization import ExecutionAuthorization
        import main as product
        app = QApplication.instance() or QApplication([])
        for name in ("msyh.ttc", "consola.ttf", "consolab.ttf", "segoeui.ttf", "segoeuib.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
            if font.exists(): QFontDatabase.addApplicationFont(str(font))
        product.initialize_desktop_theme(app)
        from core.theme import ThemeRuntimeManager, DesignTokens, default_design_tokens
        from core.theme_service import ThemeRepository
        theme = ThemeRuntimeManager(app, ThemeRepository(str(Path(temporary) / "themes")))
        app.theme_manager = theme
        theme.apply_repository_state(reason="browser_mode_qa")
        with patch.object(product.MainWindow, "showEvent", lambda self, event: QMainWindow.showEvent(self, event)):
            window = product.MainWindow(theme_manager=theme)
            try:
                window.resize(1000, 720); window.show()
                window.open_settings("实验功能")
                app.processEvents()
                page = window.product_pages[window.PAGE_SETTINGS]
                panel = page.browser_automation_panel
                # Ensure this lower section is visible in the actual scroll page.
                from PySide6.QtWidgets import QScrollArea
                ancestor = panel.parentWidget()
                while ancestor is not None and not isinstance(ancestor, QScrollArea):
                    ancestor = ancestor.parentWidget()
                if ancestor: ancestor.ensureWidgetVisible(panel)
                def capture(name):
                    QTest.qWait(120); app.processEvents()
                    if ancestor:
                        ancestor.verticalScrollBar().setValue(ancestor.verticalScrollBar().maximum())
                    QTest.qWait(80)
                    scale = os.environ.get("QT_SCALE_FACTOR", "1")
                    assert window.grab().save(str(output / f"{name}-1000-{scale}.png"))
                capture("browser-mode-default")
                auth = ExecutionAuthorization("qa", "qa-run", True, "")
                bind_run(window.config_manager, auth, worker=True)
                with browser_operation({"execution_authorization": auth}, EXTERNAL): pass
                panel.enabled.setChecked(True)
                assert panel.save()
                assert panel.coordinator.snapshot()["pending"]
                capture("browser-mode-pending")
                panel.cancel_switch()
                assert not panel.coordinator.snapshot()["pending"]
                finish_run(auth)
                panel.enabled.setChecked(True)
                with patch("ui.browser_automation_settings.ProductMessageDialog.exec_result", return_value="stay"):
                    assert not window._confirm_leave_product_page()
                with patch.object(panel.coordinator, "save", side_effect=OSError("disk full")):
                    assert not panel.save()
                assert panel.has_pending_input()
                capture("browser-mode-save-error")
                panel.reset()
                panel.enabled.setChecked(True); assert panel.save()
                capture("browser-mode-internal")
                saved = DesignTokens.primary
                theme.repository.write_preview(name="浅色浏览器设置预览", overrides={"tokens": {"primary": "#39734d", "primary_hover": "#2d603e"}}, default_tokens=default_design_tokens())
                assert theme.apply_repository_state(reason="browser_mode_preview"), theme.last_error
                capture("browser-mode-theme-preview")
                assert theme.restore_saved_theme()
                assert DesignTokens.primary == saved
                print("light settings default/pending/cancel/save failure/internal: PASS", flush=True)
            finally:
                stopped = []
                window._shutdown_workers_async(lambda _preserved: stopped.append(True))
                deadline = time.monotonic() + 30
                while not stopped and time.monotonic() < deadline:
                    app.processEvents(); QTest.qWait(20)
                assert stopped, "UI workers did not finish closing"
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__": main()

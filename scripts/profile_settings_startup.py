"""Measure one cold settings entry in a fresh process with isolated app data."""

from qa_support import prepare_qa_directory
QA_ROOT = prepare_qa_directory(__file__)
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]


def run(output, native=False, baseline=None, dark=False, scale=1, screenshot=False, audit_windows=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ.update(QT_QPA_PLATFORM="windows" if native else "offscreen",
                      QT_SCALE_FACTOR=str(scale), COWORK_EXTERNAL_BACKGROUND_SERVICES="0",
                      TEMP=str(output), TMP=str(output))
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        os.environ.update(APPDATA=temporary, LOCALAPPDATA=temporary)
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: temporary
        if baseline:
            # Before-edit snapshots allow comparison without switching a dirty checkout.
            for name, filename in (("ui.model_services", "models.before.py"),
                                   ("ui.theme_settings", "theme.before.py"), ("main", "main.before.py")):
                spec = importlib.util.spec_from_file_location(name, Path(baseline) / filename)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
        from PySide6.QtWidgets import QWidget
        from PySide6.QtCore import QEvent, QObject, Qt
        from PySide6.QtGui import QFontDatabase
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import MainWindow, SafeApplication, initialize_desktop_theme
        from ui_window_cleanup import close_test_window
        app = SafeApplication([])
        for font in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        initialize_desktop_theme(app)
        manager = ThemeRuntimeManager(app, ThemeRepository(temporary))
        app.theme_manager = manager
        assert manager.apply_repository_state()
        class WindowTracker(QObject):
            def __init__(self):
                super().__init__()
                self.events = []
                self.stage = "construct"
                self.allowed = set()

            def eventFilter(self, obj, event):
                if (event.type() == QEvent.Show and isinstance(obj, QWidget)
                        and obj.isWindow() and obj not in self.allowed
                        and obj.windowType() not in (Qt.ToolTip, Qt.Popup)):
                    self.events.append({"stage": self.stage, "class": type(obj).__name__,
                                        "object": obj.objectName(),
                                        "stack": traceback.format_stack(limit=8)})
                return False

        tracker = WindowTracker()
        if audit_windows:
            app.installEventFilter(tracker)
        with patch("core.config_manager.get_app_data_dir", return_value=temporary), \
                patch("core.config_manager.get_base_dir", return_value=temporary), \
                patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow(ConfigManager(), theme_manager=manager)
            tracker.allowed.add(window)
            window._startup_hydration_scheduled = True
            window._background_services_scheduled = True
            window._model_onboarding_shown = True
            window.skill_manager = window.skill_catalog_service.preload().manager
            window.skill_manager_ready = True
            if dark:
                assert manager.apply_profile(builtin_theme("default", dark=True), preview=True)
            window.resize(1000 if scale > 1 else 1440, 760 if scale > 1 else 900)
            window.show()
            for _ in range(10):
                app.processEvents()
            measurements = []

            def measure(name, action):
                tracker.stage = name
                started = time.perf_counter()
                result = action()
                assert result is not False, name
                returned = time.perf_counter()
                for _ in range(6):
                    app.processEvents()
                measurements.append({"name": name, "action_ms": round((returned-started)*1000, 2),
                                     "with_events_ms": round((time.perf_counter()-started)*1000, 2)})

            try:
                measure("first", window.open_settings)
                page = window.product_pages[window.PAGE_SETTINGS]
                first_count = len(page.findChildren(QWidget))
                assert not page.isWindow()
                if screenshot:
                    assert window.grab().save(str(output / "settings.png"))
                measure("back", window.show_conversation_page)
                measure("repeat", window.open_settings)
                assert window.product_pages[window.PAGE_SETTINGS] is page
                if not baseline:
                    from ui.settings_loading import PAGE_SPECS
                    assert set(page._loaded_pages) == {"appearance"}
                    for key in page._lazy_pages:
                        measure(key, lambda key=key: page.select_initial_page(PAGE_SPECS[key][0]))
                        assert key in page._loaded_pages, key
                    page.select_initial_page("外观与阅读")
                    measure("theme_editor", page.theme_settings_panel.open_editor)
                    measure("theme_advanced", page.theme_settings_panel.ensure_more)
                    if audit_windows:
                        page.select_initial_page("模型与服务")
                        channels = page.model_channel_manager.get_channels()
                        if channels:
                            measure("model_detail", lambda: page.model_channel_manager.open_service(channels[0]["channel_id"]))
                        page.select_initial_page("消息连接")
                        for provider in ("feishu", "dingtalk", "wecom", "qq", "wechat"):
                            measure("im_" + provider, lambda provider=provider: page._select_im_provider(provider))
                        if screenshot:
                            assert window.grab().save(str(output / "connection.png"))
                        page.select_initial_page("外观与阅读")
                        manager.restore_saved_theme(reason="settings_audit_restore")
                        app.processEvents()
                        assert not page._settings_dirty
                result = {"measurements": measurements, "initial_widgets": first_count,
                          "platform": app.platformName(), "dpr": window.devicePixelRatioF(),
                          "scale": scale, "dark": dark, "baseline": bool(baseline),
                          "measurement": "No cProfile; action plus six processEvents calls; isolated data, no external services",
                          "window_audit": tracker.events if audit_windows else None}
                (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False), flush=True)
                assert not tracker.events, "Unexpected native windows; see result.json"
            finally:
                manager.restore_saved_theme(reason="settings_profile_finished")
                close_test_window(window)
                manager.stop()
                if audit_windows:
                    app.removeEventFilter(tracker)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(QA_ROOT / "results"))
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--baseline")
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument("--audit-windows", action="store_true")
    run(**vars(parser.parse_args()))

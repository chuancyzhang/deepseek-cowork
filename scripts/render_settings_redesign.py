"""Render real settings controls with isolated data; no external services run."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")


def run():
    with tempfile.TemporaryDirectory(prefix="cowork-settings-qa-") as temporary:
        os.environ["APPDATA"] = temporary
        data = Path(temporary) / "DeepSeekCowork"
        data.mkdir()
        (data / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager, default_design_tokens
        from core.theme_service import ThemeRepository
        from main import MainWindow

        app = QApplication.instance() or QApplication([])
        for filename in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename))
        config = ConfigManager()
        manager = ThemeRuntimeManager(app, ThemeRepository(config.data_dir))
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="screenshot_start"), manager.last_error
        # Rendering does not need startup catalog/network workers.
        startup_patch = patch.object(MainWindow, "start_background_services", lambda self: None)
        startup_patch.start()
        window = MainWindow(config_manager=config, theme_manager=manager)
        window._model_onboarding_shown = True
        window.resize(1280, 900)
        window.show()
        output = Path(os.environ.get("COWORK_SETTINGS_SCREENSHOTS", ROOT / "docs/screenshots/settings-redesign"))
        output.mkdir(parents=True, exist_ok=True)

        def capture(name):
            QTest.qWait(160)
            path = output / name
            assert window.grab().save(str(path)), str(path)

        capture("workspace.png")
        assert window.open_settings("外观")
        page = window.product_pages[window.PAGE_SETTINGS]
        QTest.qWait(200)
        durations = []
        for key in ("builtin_paper", "builtin_forest", "builtin_mist", "default"):
            started = time.perf_counter()
            if os.environ.get("COWORK_PROFILE_THEME") and key == "builtin_paper":
                import cProfile
                profiler = cProfile.Profile()
                profiler.enable()
            manager.switch_theme(key)
            app.processEvents()
            if os.environ.get("COWORK_PROFILE_THEME") and key == "builtin_paper":
                profiler.disable()
                import pstats
                with (output / "profile.txt").open("w", encoding="utf-8") as stream:
                    pstats.Stats(profiler, stream=stream).sort_stats("cumtime").print_stats(45)
            durations.append({"theme": key, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)})
        for label, filename in (
            ("外观与阅读", "appearance.png"), ("回答偏好", "preferences.png"),
            ("模型与服务", "models.png"), ("消息连接", "connections.png"),
            ("企业账号", "account.png"), ("数据安全", "privacy.png"),
            ("变量与凭据", "credentials.png"), ("文件与存储", "storage.png"),
            ("实验功能", "advanced.png"), ("组件与依赖", "components.png"),
            ("运行日志", "logs.png"), ("更新与关于", "about.png"),
        ):
            if os.environ.get("COWORK_PROFILE_THEME") or (os.environ.get("COWORK_SETTINGS_SCALE_ONLY") and filename != "appearance.png"):
                continue
            page.select_initial_page(label)
            capture(filename)
        page.select_initial_page("外观")
        manager.switch_theme("default", mode="light")
        window.resize(760, 760)
        capture("narrow.png")
        window.resize(1280, 900)
        candidate = manager.repository.write_preview(
            name="苔绿候选", overrides={"tokens": {"primary": "#39735a", "bg_main": "#f7faf6", "bg_sidebar": "#e8efe7"}},
            default_tokens=default_design_tokens(), session_id="screenshot-theme-task")
        manager.request_preview_update()
        deadline = time.monotonic() + 5
        while manager.preview is None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert manager.preview and manager.preview["preview_id"] == candidate["preview_id"]
        appearance = page._settings_pages[0]
        QTest.qWait(150)
        appearance.verticalScrollBar().setValue(appearance.verticalScrollBar().maximum())
        capture("candidate.png")
        (output / "timing.json").write_text(json.dumps({
            "scope": "isolated desktop settings, empty history, built-in palettes; not a production benchmark",
            "scale": os.environ.get("QT_SCALE_FACTOR", "1"), "switches": durations,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(durations), flush=True)
        manager.stop()
        page._allow_close_without_prompt = True
        window.close()
        deadline = time.monotonic() + 10
        while not getattr(window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
            QTest.qWait(20)
        assert getattr(window, "_checkpoint_close_ready", False), "screenshot window did not finish shutdown"
        window.file_model.directoryLoaded.disconnect()
        window.deleteLater()
        app.processEvents()
        import gc
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        del page, window
        gc.collect()
        startup_patch.stop()


if __name__ == "__main__":
    run()

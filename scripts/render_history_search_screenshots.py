"""Real sidebar search with isolated synthetic history, scaling and theme QA."""

import argparse
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots/history-search")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-history-search-qa-") as root:
        os.environ["APPDATA"] = str(Path(root) / "appdata")
        config_dir = Path(os.environ["APPDATA"]) / "DeepSeekCowork"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        from core.theme import ThemeRuntimeManager, DesignTokens, default_design_tokens
        from core.theme_service import ThemeRepository
        import main

        app = QApplication.instance() or QApplication([])
        for filename in ("msyh.ttc", "consola.ttf", "consolab.ttf", "segoeui.ttf", "segoeuib.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
        main.initialize_desktop_theme(app)
        theme = ThemeRuntimeManager(app, ThemeRepository(str(Path(root) / "themes")))
        app.theme_manager = theme
        theme.apply_repository_state(reason="search_qa")
        window = main.MainWindow(theme_manager=theme)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        scale = os.environ.get("QT_SCALE_FACTOR", "1")

        def wait(predicate, timeout=15):
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                time.sleep(0.005)
            assert predicate(), "search UI did not reach the expected state"

        def settle(seconds=0.1):
            deadline = time.monotonic() + seconds
            wait(lambda: time.monotonic() >= deadline)

        def capture(name, width=1000, height=720):
            window.resize(width, height)
            settle()
            assert window.history_search_input.width() > 100
            assert window.history_scroll.isVisible()
            assert window.grab().save(str(args.output / f"{name}-{scale}.png"), "PNG")

        def search(text):
            window.history_search_input.setFocus()
            window.history_search_input.setText(text)
            wait(lambda: not window.history_search_timer.isActive() and window.history_search_controller.worker is None)

        try:
            project = Path(root) / "产品资料"
            project.mkdir()
            window.config_manager.set_projects([{"path": str(project), "name": "产品资料"}])
            for index in range(62):
                window.chat_storage.save_conversation(
                    f"search-example-{index}", [{"id": f"message-{index}", "role": "assistant",
                                                "content": "已完成截图整理，并把归档说明保存到项目文件夹。"}],
                    title=("产品页面检查" if index < 4 else f"资料整理 · 第 {index + 1} 次"),
                    meta={"workspace_dir": str(project), "workspace_source": "project"} if index < 4 else {"workspace_source": "chat"},
                )
            window.resize(1000, 720)
            window.show()
            window.refresh_history_list()
            search("截图")
            assert len(window.history_rows) == 50
            capture("results")
            capture("results-small", 800, 600)
            window.history_disclosure_buttons[("search", "")].click()
            assert len(window.history_rows) == 62

            release = threading.Event()
            original_search = window.chat_storage.search_sidebar_conversations

            def held_search(*a, **kw):
                release.wait(5)
                return original_search(*a, **kw)

            with patch.object(window.chat_storage, "search_sidebar_conversations", side_effect=held_search):
                window.history_search_input.setText("归档")
                wait(lambda: window.history_search_controller.worker is not None)
                try:
                    capture("loading", 800, 600)
                finally:
                    release.set()
                wait(lambda: window.history_search_controller.worker is None)

            with patch.object(window.chat_storage, "search_sidebar_conversations", side_effect=sqlite3.OperationalError("locked")):
                search("整理")
            assert not window.history_rows
            assert window.history_search_input.text() == "整理"
            capture("failure", 800, 600)
            window.refresh_history_list()
            wait(lambda: window.history_search_controller.worker is None)
            assert len(window.history_rows) == 50

            search("不存在的关键词")
            assert not window.history_rows
            capture("empty", 800, 600)
            search("截图")
            saved_primary = DesignTokens.primary
            theme.repository.write_preview(name="搜索主题检查", overrides={"tokens": {
                "primary": "#39734d", "primary_hover": "#2d603e",
            }}, default_tokens=default_design_tokens())
            assert theme.apply_repository_state(reason="search_preview"), theme.last_error
            wait(lambda: window.history_search_controller.worker is None)
            assert len(window.history_rows) == 50
            capture("theme-preview", 800, 600)
            assert theme.restore_saved_theme()
            assert DesignTokens.primary == saved_primary
            window.history_search_input.clear()
            assert window._history_search_result is None
            assert window._history_search_loading_label is None
            print(f"history search: results, paging, loading, failure/empty, new search, clear, theme restore PASS; scale={scale}")
        finally:
            finished = []
            window._shutdown_workers_async(lambda _preserved: finished.append(True))
            wait(lambda: bool(finished))
            window._checkpoint_close_ready = True
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    run()

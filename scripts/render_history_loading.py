"""Verify history loading and recorded timing with isolated native/offscreen UI."""

from qa_support import prepare_qa_directory
QA_ROOT = prepare_qa_directory(__file__)
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]


def run(output, scale, native):
    temporary_root = ROOT / ".tmp" / "history-loading-ux"
    temporary_root.mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(temporary_root), TMP=str(temporary_root),
                      QT_QPA_PLATFORM="windows" if native else "offscreen",
                      QT_SCALE_FACTOR=str(scale), COWORK_EXTERNAL_BACKGROUND_SERVICES="0")
    tempfile.tempdir = str(temporary_root)
    with tempfile.TemporaryDirectory(prefix="history-ui-") as temporary:
        os.environ.update(APPDATA=temporary, LOCALAPPDATA=temporary)
        data = Path(temporary, "DeepSeekCowork")
        data.mkdir()
        (data / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication, QWidget, QPushButton
        from core.config_manager import ConfigManager
        from core.theme import DesignTokens, ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import MainWindow, ThinkingTextView
        from ui.history_presentation import RUN_ELAPSED_META_KEY
        from ui_window_cleanup import close_test_window

        app = QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts", name)))
        config = ConfigManager()
        repository = ThemeRepository(str(Path(temporary, "DeepSeekCowork")))
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="history_loading_qa"), manager.last_error
        saved_path = Path(repository.store_path)
        saved_before = saved_path.read_bytes() if saved_path.exists() else None

        class Tracker(QObject):
            allowed = None
            events = []

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and obj is not self.allowed:
                    self.events.append({"class": type(obj).__name__, "name": obj.objectName(),
                                        "stack": traceback.format_stack(limit=7)})
                return False

        tracker = Tracker(app)
        app.installEventFilter(tracker)
        window = MainWindow(config_manager=config, theme_manager=manager)
        tracker.allowed = window
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        window._model_onboarding_shown = True
        window.resize(1360, 860)
        window.show()
        output.mkdir(parents=True, exist_ok=True)

        def settle():
            deadline = time.monotonic() + .2
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)

        def capture(name):
            settle()
            path = output / f"{name}-{scale:g}x{'-windows' if native else ''}.png"
            assert window.grab().save(str(path))
            print(path, flush=True)

        state = window.get_current_session()
        fixture = [
            {"id": "history-user", "role": "user", "content": "请整理会议讨论，保留结论和下一步行动。"},
            {"id": "history-answer", "role": "assistant", "content": "已整理本次讨论的主要结论和待办。\n\n### 主要结论\n\n统一任务入口，保留用户输入和已经完成的成果。\n\n### 下一步\n\n1. 核对待办的负责人和时间。\n2. 补充尚未确认的事项。\n3. 用实际任务验证完整流程。",
             "reasoning_content": "先核对讨论中的结论，再整理负责人和下一步行动。未确认的事项单独保留。",
             "meta": {"request_id": "history-run", "ui_reply_kind": "final"}},
        ]
        window.chat_storage.upsert_conversation(state.session_id, title="整理会议讨论", status="completed")
        state.persisted_conversation_meta = {"manual_title": True}
        window.update_session_tab_title(state.session_id)
        window.refresh_history_list()
        state.history_loaded = False
        state.history_loading = True
        state.history_load_token = 71
        state.composer_draft = "请补充待确认事项。"
        window.input_field.setPlainText(state.composer_draft)
        window._show_session_loading_state(state)
        window.normalize_session_ui(state)
        window.show_conversation_page()
        try:
            capture("loading-light")
            window._handle_session_history_loaded({"ok": True, "session_id": state.session_id, "token": 71,
                "messages": fixture, "conversation_meta": {"manual_title": True, RUN_ELAPSED_META_KEY: {"history-run": 119}},
                "conversation_record": {"title": "整理会议讨论", "status": "completed"}})
            deadline = time.monotonic() + 8
            while state.history_loading_widget is not None and time.monotonic() < deadline:
                settle()
            assert state.history_loaded and state.history_loading_widget is None
            assert window.input_field.toPlainText() == "请补充待确认事项。"
            summary = state.history_detail_summaries[0]
            assert summary.detail_button.text() == "用时 1分钟 59秒"
            capture("ready-light")
            summary.detail_button.click()
            settle()
            assert summary.detail_groups and not summary.detail_loading
            bubble = summary.detail_groups[0].stage_bubbles[0]
            thought = bubble.findChild(ThinkingTextView)
            assert thought.text() == fixture[1]["reasoning_content"]
            bubble.think_toggle_btn.click()
            settle()
            assert bubble.think_container.isVisible()
            capture("process-light")
            summary.detail_button.click()
            other = window.create_new_session(make_current=False)
            for sid in (other, state.session_id, other, state.session_id):
                window.set_current_session(sid)
                settle()
            assert window.input_field.toPlainText() == "请补充待确认事项。"
            window.resize(980, 760)
            assert manager.apply_profile(builtin_theme("default", dark=True), preview=True)
            capture("ready-dark-narrow")
            state.history_loaded = False
            state.history_loading = True
            window._show_session_loading_state(state)
            window.normalize_session_ui(state)
            capture("loading-dark-narrow")
            assert state.history_loading_widget.geometry() == state.chat_scroll.viewport().rect()
            state.history_loading = False
            window._show_session_load_error_state(state, "读取记录失败，请重试。")
            window.normalize_session_ui(state)
            capture("error-dark-narrow")
            assert any(b.text() == "重试" for b in state.chat_layout.parentWidget().findChildren(QPushButton))
            assert manager.restore_saved_theme(reason="history_loading_qa_restore")
            settle()
            assert (saved_path.read_bytes() if saved_path.exists() else None) == saved_before
            assert not tracker.events, tracker.events
            report = {"platform": app.platformName(), "qt_scale_factor": scale,
                      "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
                      "device_pixel_ratio": window.devicePixelRatioF(),
                      "unexpected_native_windows": tracker.events, "theme_saved_state_unchanged": True,
                      "checks": ["loading", "ready", "existing_thoughts", "repeated_switch", "draft_preserved",
                                 "narrow_dark", "load_error", "theme_preview_cancel"]}
            (output / f"report-{scale:g}x{'-windows' if native else ''}.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        finally:
            app.removeEventFilter(tracker)
            close_test_window(window)
            manager.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=QA_ROOT / "screenshots")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    run(args.output, args.scale, args.native)

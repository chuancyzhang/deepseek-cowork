"""Exercise message editing and tool-file links with an isolated real workspace."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))


def run(platform, scale):
    os.environ.update(QT_QPA_PLATFORM=platform, QT_SCALE_FACTOR=str(scale),
                      COWORK_EXTERNAL_BACKGROUND_SERVICES="0", QT_OPENGL="software",
                      QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-gpu-compositing")
    with tempfile.TemporaryDirectory(prefix="cowork-file-links-") as temporary:
        os.environ.update(APPDATA=temporary, LOCALAPPDATA=temporary)
        isolated_config = Path(temporary, "DeepSeekCowork", "config.json")
        isolated_config.parent.mkdir()
        isolated_config.write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QEvent, QObject, QPoint, Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QWidget
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import ChatBubble, MainWindow
        from ui_window_cleanup import close_test_window

        app = QApplication([])
        for font in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows"), "Fonts", font)))
        config = ConfigManager()
        repository = ThemeRepository(config.data_dir)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="file_link_check"), manager.last_error
        saved = repository.load()

        class ShowTracker(QObject):
            def __init__(self):
                super().__init__()
                self.unexpected = []

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and not isinstance(obj, MainWindow):
                    self.unexpected.append((type(obj).__name__, obj.objectName()))
                return False

        tracker = ShowTracker()
        app.installEventFilter(tracker)
        with patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow(config_manager=config, theme_manager=manager)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        window._model_onboarding_shown = True
        available = app.primaryScreen().availableGeometry()
        window.resize(min(1440, available.width() - 40) if platform == "windows" else 1440,
                      min(900, available.height() - 60) if platform == "windows" else 900)
        window.show()
        output = ROOT / "docs/images/conversation-file-navigation"
        output.mkdir(parents=True, exist_ok=True)
        suffix = f"{platform}-{scale:g}x"

        def settle():
            deadline = time.monotonic() + .18
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)

        def capture(name):
            settle()
            if name.startswith("file-preview"):
                window.input_field.setFocus()
                state.chat_scroll.verticalScrollBar().setValue(state.chat_scroll.verticalScrollBar().minimum())
                settle()
            assert window.grab().save(str(output / f"{name}-{suffix}.png"))

        try:
            state = window.get_current_session()
            workspace = Path(temporary, "腾讯全球大会", "20261030")
            workspace.mkdir(parents=True)
            state.workspace_dir = str(workspace)
            path = workspace / "01_组织AI协作与权限治理_会议纪要.md"
            path.write_text("# 组织 AI 协作与权限治理\n\n统一术语：WorkBuddy。\n\n- 保留原始发言含义。\n- 按章节修订会议纪要。", encoding="utf-8")
            second = workspace / "02_后续安排.md"
            second.write_text("# 后续安排\n\n核对术语与正文。", encoding="utf-8")
            user = window.add_chat_bubble("User", "你好", source_message_id="user", animate=False)
            state.messages = [{"id": "user", "role": "user", "content": "你好"}]
            window.normalize_session_ui(state)
            user.begin_inline_edit()
            user.user_content_edit.setPlainText(r"针对D:\期货\腾讯全球大会\20261030这里的文档")
            settle()
            assert user.user_content_edit.width() > 400
            capture("inline-edit")
            user.cancel_inline_edit()
            assert user.user_content_edit.toPlainText() == "你好"
            state.last_agent_bubble = window.add_chat_bubble("Agent", "先读取会议纪要，再统一术语并修订正文。", animate=False)
            tool_specs = [
                ("read", "text_file_read", {"path": str(path)}),
                ("patch", "apply_patch", {"patch": f"*** Begin Patch\n*** Update File: {path.name}\n@@\n-Workbuddy\n+WorkBuddy\n*** Update File: {second.name}\n@@\n-旧内容\n+新内容\n*** End Patch"}),
                ("delete", "workspace_delete_path", {"path": "已删除的临时稿.md"}),
            ]
            for tool_id, name, args in tool_specs:
                window.add_tool_card({"id": tool_id, "name": name, "args": args}, session_id=state.session_id, animate=False)
                window.update_tool_card({"id": tool_id, "result": "已完成", "result_obj": {"status": "success"}}, session_id=state.session_id)
            settle()
            card = state.tool_cards["read"]
            label = card.tool_context_label
            label.setFocus()
            # Activate the actual rendered anchor with a mouse click.
            QTest.mouseClick(label, Qt.LeftButton, pos=QPoint(label.fontMetrics().horizontalAdvance("text_file_read · ") + 8,
                                                           label.fontMetrics().height() // 2))
            settle()
            assert window.right_drawer_tab == window.RIGHT_TAB_FILES
            assert window.current_deliverable_path == str(path)
            capture("file-preview")
            state.tool_cards["patch"].tool_context_label.linkActivated.emit("tool-file:1")
            settle()
            assert window.current_deliverable_path == str(second)
            with patch.object(window, "add_system_toast") as notice:
                state.tool_cards["delete"].tool_context_label.linkActivated.emit("tool-file:0")
                assert "文件已删除" in notice.call_args.args[0]
            assert window.current_deliverable_path == str(second)
            card.view_btn.click()
            settle()
            assert window.current_selected_tool_id == "read"
            assert window.td_result_edit.isVisible()
            window.hide_context_drawer()
            other = window.create_new_session(make_current=False)
            window.set_current_session(other)
            with patch.object(window, "select_deliverable") as select:
                card.tool_context_label.linkActivated.emit("tool-file:0")
                select.assert_not_called()
            window.set_current_session(state.session_id)
            settle()
            card.tool_context_label.linkActivated.emit("tool-file:0")
            assert window.current_deliverable_path == str(path)
            assert manager.apply_profile(builtin_theme("default", dark=True), preview=True)
            window.resize(min(1100, window.width()), min(760, window.height()))
            settle()
            for item in state.tool_cards.values():
                assert item.tool_context_label.width() <= item.width()
                assert item.main_row.geometry().right() < item.width() + 1
                assert item.width() >= state.last_agent_bubble.content_col.width() - 12
            capture("file-preview-dark-compact")
            window.hide_context_drawer()
            user.begin_inline_edit()
            user.user_content_edit.setPlainText("小窗口编辑中的文字，切换主题时继续保留。")
            assert manager.restore_saved_theme(reason="file_link_preview_cancel")
            settle()
            assert not user.user_content_edit.isReadOnly()
            assert "小窗口编辑" in user.user_content_edit.toPlainText()
            user.cancel_inline_edit()
            assert repository.load() == saved

            # Replay a persisted tool call and expand its history details.
            fixture = [
                {"id": "history-user", "role": "user", "content": "读取会议纪要"},
                {"id": "history-call", "role": "assistant", "content": "", "tool_calls": [
                    {"id": "history-read", "type": "function", "function": {
                        "name": "text_file_read", "arguments": json.dumps({"path": str(path)})}}]},
                {"id": "history-result", "role": "tool", "tool_call_id": "history-read", "content": "会议纪要正文"},
                {"id": "history-answer", "role": "assistant", "content": "已读取会议纪要。", "meta": {"ui_reply_kind": "final"}},
            ]
            for _ in range(2):
                window._handle_session_history_loaded({"ok": True, "session_id": state.session_id,
                    "token": state.history_load_token, "messages": fixture, "conversation_meta": {},
                    "conversation_record": {"title": "历史文件操作", "status": "completed"}})
                settle()
                for summary in state.history_detail_summaries:
                    summary.detail_button.setChecked(True)
                settle()
                state.tool_cards["history-read"].tool_context_label.linkActivated.emit("tool-file:0")
                assert window.current_deliverable_path == str(path)
                restored_user = next(b for b in window._iter_session_chat_bubbles() if b.role == "User" and b.source_message_id == "history-user")
                restored_user.begin_inline_edit()
                settle()
                assert restored_user.user_content_edit.width() > 200
                restored_user.cancel_inline_edit()
            assert not tracker.unexpected, tracker.unexpected
            print(json.dumps({"platform": platform, "qt_scale_factor": scale,
                "device_pixel_ratio": window.devicePixelRatioF(), "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
                "window_size": window.size().toTuple(), "unexpected_windows": tracker.unexpected,
                "history_reloads": 2, "result": "passed"}, ensure_ascii=False))
        finally:
            manager.stop()
            close_test_window(window)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("offscreen", "windows"), default="offscreen")
    parser.add_argument("--scale", type=float, default=1)
    args = parser.parse_args()
    run(args.platform, args.scale)

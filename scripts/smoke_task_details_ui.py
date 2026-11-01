"""Render real task-detail widgets with synthetic data and isolated settings."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QObject, QThreadPool
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QWidget, QToolButton
from core.config_manager import ConfigManager
from core.request_snapshot import RequestSnapshotStore
from core.theme import DesignTokens, ThemeRuntimeManager
from core.theme_service import ThemeRepository
from core.theme_presets import builtin_theme
from main import MainWindow, SystemToast


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/images/task-details")
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for filename in ("msyh.ttc", "consola.ttf", "consolab.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Microsoft YaHei UI", 9))
    errors = []
    old_hook = sys.excepthook
    sys.excepthook = lambda typ, exc, tb: errors.append(str(exc))
    with tempfile.TemporaryDirectory(prefix="cowork-details-smoke-") as root:
        with patch("core.config_manager.get_app_data_dir", return_value=root), \
             patch("core.config_manager.get_base_dir", return_value=root), \
             patch("main.get_app_data_dir", return_value=root), \
             patch.object(MainWindow, "start_background_services", new=lambda self: None), \
             patch.object(MainWindow, "_show_model_onboarding_if_needed", new=lambda self: None):
            repository = ThemeRepository(root)
            theme = ThemeRuntimeManager(app, repository)
            app.theme_manager = theme
            theme.apply_repository_state()
            saved_path = Path(repository.store_path)
            saved_before = saved_path.read_bytes() if saved_path.exists() else None
            saved_background = DesignTokens.bg_main
            if args.dark:
                assert theme.apply_profile(builtin_theme("default", dark=True), preview=True), theme.last_error
            class NativeShowTracker(QObject):
                def __init__(self):
                    super().__init__()
                    self.allowed = set()
                    self.events = []

                def eventFilter(self, obj, event):
                    if (event.type() == QEvent.Show and isinstance(obj, QWidget)
                            and obj.isWindow() and obj not in self.allowed):
                        self.events.append({"class": type(obj).__name__, "name": obj.objectName(),
                                            "stack": traceback.format_stack()[-8:]})
                    return False

            tracker = NativeShowTracker()
            app.installEventFilter(tracker)
            window = MainWindow(ConfigManager(), theme_manager=theme)
            window._startup_hydration_scheduled = True
            window._background_services_scheduled = True
            tracker.allowed.add(window)
            window.resize(980 if args.compact else 1360, 740 if args.compact else 880)
            window.show()
            if args.dark:
                assert theme.apply_profile(builtin_theme("default", dark=True), preview=True), theme.last_error
                assert DesignTokens.bg_main != saved_background
            state = window.get_current_session()
            window.chat_storage.upsert_conversation(state.session_id, title="分析今天的股市行情")
            window.add_chat_bubble("User", "整理市场资料，核对来源后生成分析报告。", animate=False)
            window.add_chat_bubble("agent", "正在核对资料来源。可以打开工具查看具体参数和结果，也可以在工作区查看子任务进展。", animate=False)
            RequestSnapshotStore(window.chat_history_dir).capture(state.session_id, "sample-run", "sample-request",
                "chat_completions", {"model": "示例模型", "messages": [{"role": "user", "content": "分析今天的股市行情"}]})
            state.step_records = [
                {"tool_id": "1", "display_title": "搜索市场资料", "status": "done", "duration": 3.5, "summary": "已获取搜索结果，点击查看完整内容"},
                {"tool_id": "2", "display_title": "查询搜索域名", "status": "error", "duration": 0.3, "summary": "缺少必要参数：domain"},
                {"tool_id": "3", "display_title": "生成市场分析", "status": "running", "summary": "等待返回结果"},
            ]
            window.refresh_step_list(state.session_id)
            window.show_tool_details("2", {"query": "今日 A 股市场行情"},
                {"ok": False, "error": "domain required", "message": "缺少必要参数：domain"},
                {"duration": 0.3})
            QTest.qWait(120)
            frame = window.file_tab_strip._tab_frames[window.file_tab_strip._path_key("@task")]
            close = frame.findChild(QToolButton, "FileTabClose")
            viewport = window.file_tab_strip.scroll.viewport()
            assert viewport.rect().contains(close.mapTo(viewport, close.rect().center()))
            assert window.right_sidebar.grab().save(str(output / "technical-details.png"))
            assert window.grab().save(str(output / "unified-workspace.png"))
            assert not hasattr(window, "td_raw_details")
            assert not hasattr(window, "td_exported_files")
            window.set_workspace_expanded(True)
            QTest.qWait(60)
            assert window.grab().save(str(output / "workspace-expanded.png"))
            window.select_workspace_conversation()
            QTest.qWait(40)
            assert window.grab().save(str(output / "workspace-conversation.png"))
            window.open_task_workspace()
            window.set_workspace_expanded(False)
            window.set_observability_section(window.OBS_SECTION_LOG)
            QTest.qWait(40)
            assert window.right_sidebar.grab().save(str(output / "call-records.png"))
            assert window.step_list.horizontalScrollBar().maximum() == 0
            with patch.object(window.sub_agent_monitor, "_request"):
                window.open_task_workspace(window.OBS_SECTION_AGENTS)
                window.sub_agent_monitor.set_summaries([
                    {"id": "sources", "name": "核对资料来源", "status": "running", "created_at": 1,
                     "latest_progress": "正在检查原始引用与发布时间。", "duration": 12},
                    {"id": "report", "name": "整理分析报告", "status": "completed", "created_at": 2,
                     "last_result": "已整理报告结构，等待来源核对结果。", "duration": 8},
                ])
                QTest.qWait(40)
                assert window.right_sidebar.grab().save(str(output / "subtasks.png"))
                panel = window.sub_agent_monitor
                panel.open_agent("sources")
                panel.messages = {
                    "sample-user": {"id": "sample-user", "role": "user", "content": "请核对报告中的资料来源。"},
                    "sample-answer": {"id": "sample-answer", "role": "assistant", "content": "已核对两份原始资料，正在检查剩余引用。"},
                }
                panel._render()
                panel.editor.setPlainText("优先核对发布日期。")
                QTest.qWait(60)
                assert window.right_sidebar.grab().save(str(output / "subtask-conversation.png"))
            window.open_task_workspace(window.OBS_SECTION_PROMPT)
            QTest.qWait(40)
            assert window.right_sidebar.grab().save(str(output / "execution-context.png"))
            assert not tracker.events, tracker.events
            app.removeEventFilter(tracker)

            def capture_menu(menu, *_args):
                menu.show()
                app.processEvents()
                assert menu.grab().save(str(output / "conversation-menu.png"))
                menu.hide()
            class CaptureMenu(QMenu):
                def exec(self, *positions):
                    capture_menu(self, *positions)
            with patch("main.create_styled_menu", new=lambda parent: CaptureMenu(parent)):
                window.show_session_menu(state.session_id, window)

            toast = SystemToast("“分析今天的股市行情”的聊天记录已保存：\n聊天记录-市场分析.log", "success",
                actions=(("打开文件", lambda: None), ("打开所在文件夹", lambda: None)))
            toast.show()
            QTest.qWait(30)
            assert toast.grab().save(str(output / "export-saved.png"))
            toast.close()
            toast.deleteLater()
            app.processEvents()
            window._subagent_poll.stop()
            window.sub_agent_monitor.timer.stop()
            assert QThreadPool.globalInstance().waitForDone(5000)
            window.file_model.directoryLoaded.disconnect()
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "test"))
            from ui_window_cleanup import close_test_window
            close_test_window(window)
            theme.stop()
            if args.dark:
                theme.restore_saved_theme(reason="task_details_smoke_restore")
                assert DesignTokens.bg_main == saved_background
            assert (saved_path.read_bytes() if saved_path.exists() else None) == saved_before
    sys.excepthook = old_hook
    if errors:
        raise AssertionError(errors)
    print(json.dumps({"screenshots": str(output), "qt_errors": errors,
                      "native_show_events": tracker.events, "platform": app.platformName(),
                      "qt_scale_factor": args.scale, "screen_dpr": app.primaryScreen().devicePixelRatio()}, ensure_ascii=False))


if __name__ == "__main__":
    main()

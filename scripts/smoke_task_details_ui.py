"""Render real task-detail widgets with synthetic data and isolated settings."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QWidget, QVBoxLayout
from core.config_manager import ConfigManager
from core.request_snapshot import RequestSnapshotStore
from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_service import ThemeRepository
from main import MainWindow, SystemToast


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/images/task-details")
    parser.add_argument("--dark", action="store_true")
    args = parser.parse_args()
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
             patch.object(MainWindow, "start_background_services", new=lambda self: None), \
             patch.object(MainWindow, "_show_model_onboarding_if_needed", new=lambda self: None):
            repository = ThemeRepository(root)
            theme = ThemeRuntimeManager(app, repository)
            app.theme_manager = theme
            theme.apply_repository_state()
            if args.dark:
                repository.write_preview(name="深色验收", session_id="task-details-smoke",
                    overrides={"tokens": {"bg_main": "#17191e", "bg_secondary": "#23262e",
                        "bg_app": "#17191e", "bg_code": "#23262e", "bg_panel": "#23262e",
                        "bg_card": "#23262e", "bg_tertiary": "#30343d",
                        "bg_hover": "#30343d", "text_primary": "#eceef3", "text_secondary": "#c0c5d0",
                        "text_tertiary": "#a0a8b7", "border_subtle": "#434956", "separator": "#434956"}},
                    default_tokens=default_design_tokens())
                assert theme.apply_repository_state(reason="task_details_smoke")
            window = MainWindow(ConfigManager(), theme_manager=theme)
            state = window.get_current_session()
            window.chat_storage.upsert_conversation(state.session_id, title="分析今天的股市行情")
            RequestSnapshotStore(window.chat_history_dir).capture(state.session_id, "sample-run", "sample-request",
                "chat_completions", {"model": "示例模型", "messages": [{"role": "user", "content": "分析今天的股市行情"}]})
            window.show_tool_details("example-search", {"query": "今日 A 股市场行情"},
                {"ok": False, "error": "domain required", "message": "缺少必要参数：domain"},
                {"duration": 0.3})
            # Reuse the production header and detail page at a deterministic narrow width.
            preview = QWidget()
            preview.setObjectName("TaskDetailsPreview")
            preview.setStyleSheet(f"QWidget#TaskDetailsPreview {{ background: {DesignTokens.bg_main}; }}")
            layout = QVBoxLayout(preview)
            layout.addWidget(window.right_title_block)
            window.right_title_block.setMaximumHeight(70)
            layout.addWidget(window.tool_details_tab, 1)
            window.tool_details_tab.show()
            preview.resize(520, 740)
            preview.show()
            QTest.qWait(120)
            assert preview.grab().save(str(output / "technical-details.png"))
            assert not hasattr(window, "td_raw_details")
            assert not hasattr(window, "td_exported_files")
            state.step_records = [
                {"tool_id": "1", "display_title": "搜索市场资料", "status": "done", "duration": 3.5, "summary": "已获取搜索结果，点击查看完整内容"},
                {"tool_id": "2", "display_title": "查询搜索域名", "status": "error", "duration": 0.3, "summary": "缺少必要参数：domain"},
                {"tool_id": "3", "display_title": "生成市场分析", "status": "running", "summary": "等待返回结果"},
            ]
            window.refresh_step_list(state.session_id)
            window.set_observability_section(window.OBS_SECTION_LOG)
            QTest.qWait(40)
            assert preview.grab().save(str(output / "call-records.png"))
            assert window.step_list.horizontalScrollBar().maximum() == 0

            def capture_menu(menu, *_args):
                menu.show()
                app.processEvents()
                assert menu.grab().save(str(output / "conversation-menu.png"))
                menu.hide()
            class CaptureMenu(QMenu):
                def exec(self, *positions):
                    capture_menu(self, *positions)
            with patch("main.create_styled_menu", new=lambda parent: CaptureMenu(parent)):
                window.show_session_menu(state.session_id, preview)

            toast = SystemToast("“分析今天的股市行情”的聊天记录已保存：\n聊天记录-市场分析.log", "success",
                actions=(("打开文件", lambda: None), ("打开所在文件夹", lambda: None)))
            toast.show()
            QTest.qWait(30)
            assert toast.grab().save(str(output / "export-saved.png"))
            toast.close()
            toast.deleteLater()
            preview.close()
            app.processEvents()
            window.file_model.directoryLoaded.disconnect()
            window.close()
            window.deleteLater()
            preview.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            theme.stop()
            if args.dark:
                theme.restore_saved_theme(reason="task_details_smoke_restore")
    sys.excepthook = old_hook
    if errors:
        raise AssertionError(errors)
    print(json.dumps({"screenshots": str(output), "qt_errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()

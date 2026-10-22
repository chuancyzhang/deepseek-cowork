"""Render isolated workspace fixtures without using personal data or model calls."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def render(mode="light", scale=1, compact=False):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    with tempfile.TemporaryDirectory(prefix="cowork-workspace-preview-") as temporary:
        os.environ["APPDATA"] = str(Path(temporary, "roaming"))
        os.environ["LOCALAPPDATA"] = str(Path(temporary, "local"))
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: temporary
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        from core.theme_presets import builtin_theme
        from main import MainWindow

        app = QApplication([])
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        background_patch = patch.object(MainWindow, "start_background_services", lambda self: None)
        background_patch.start()
        with patch("core.config_manager.get_app_data_dir", return_value=temporary), patch("core.config_manager.get_base_dir", return_value=temporary):
            config = ConfigManager()
            window = MainWindow(config_manager=config, theme_manager=manager)
        window._model_onboarding_shown = True
        window.skill_manager.load_skills(load_mcp_tools=False)
        window.skill_manager_ready = True
        window.refresh_selected_skill_controls()
        window.resize(1100 if compact else 1440, 760 if compact else 900)
        window.show()
        state_path = Path(repository.store_path)
        saved_before = state_path.read_bytes() if state_path.exists() else None
        assert manager.apply_profile(builtin_theme("default", dark=mode == "dark"), preview=True), manager.last_error
        output = ROOT / "docs" / "images" / "workspace-redesign"
        output.mkdir(parents=True, exist_ok=True)

        def settle():
            QTest.qWait(180)
            window.sync_context_drawer_layout()
            QTest.qWait(100)

        def capture(name):
            settle()
            destination = output / f"{name}-{mode}-{int(scale * 100)}{'-compact' if compact else ''}.png"
            assert window.grab().save(str(destination))
            print(destination, flush=True)

        for index, title in enumerate(("整理产品方案与待办", "分析季度经营数据")):
            window.chat_storage.upsert_conversation(f"preview-recent-{index}", title=title, status="completed", meta={"workspace_source": "chat"})
        window.refresh_history_list()
        capture("home")
        window.input_field.setPlainText("保留的草稿：请补充说明数据来源。")
        current = window.get_current_session()
        current.messages = [
            {"id": "preview-user", "role": "user", "content": "请整理本次产品讨论，给出结论和下一步建议。"},
            {"id": "preview-answer", "role": "assistant", "content": "已整理讨论记录。优先统一任务入口，让项目、资料与产物之间的关系更清楚。\n\n### 建议按以下顺序推进\n\n1. 明确用户进入页面后的第一步操作。\n2. 将低频配置放入就近的选项菜单。\n3. 保留执行过程、文件预览与错误恢复入口。\n\n下一轮可以用一个真实任务验证完整流程。", "meta": {"ui_reply_kind": "final"}},
        ]
        window.add_chat_bubble("User", current.messages[0]["content"], source_message_id="preview-user", animate=False)
        window.add_chat_bubble("Agent", current.messages[1]["content"], source_message_id="preview-answer", animate=False)
        window.normalize_session_ui(current)
        assert window.input_field.toPlainText().startswith("保留的草稿")
        assert window.input_row.parentWidget() is window.conversation_column
        capture("conversation")
        current.system_prompt_text = "示例系统上下文。此截图使用隔离数据，不发送模型请求。"
        current.runtime_context_text = "示例工作目录与会话资料。"
        window.show_context_drawer(window.RIGHT_TAB_OBSERVABILITY)
        window.set_observability_section(window.OBS_SECTION_PROMPT)
        capture("context")
        window.show_tool_details("example-read-file", {"path": "产品讨论.md"}, "已读取 3 个章节。\n\n决策：统一任务入口。", meta={"duration": .18})
        capture("tool-details")
        preview_path = Path(window._workspace_dir_for_state(current)) / "产品讨论.txt"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text("产品讨论\n\n结论：统一任务入口。\n\n下一步\n1. 明确当前任务。\n2. 保留输入与成果。\n3. 验证停止和恢复入口。\n", encoding="utf-8")
        window.show_context_drawer(window.RIGHT_TAB_FILES)
        window.select_deliverable(str(preview_path), render_html=False)
        window.set_file_navigator_visible(False, reason="screenshot")
        capture("workspace")
        window.set_file_navigator_visible(True, reason="screenshot")
        capture("file-directory")
        window.set_file_navigator_visible(False, reason="screenshot")
        window.hide_context_drawer()
        window._show_composer_permission()
        capture("permission")
        window.permission_popover.close()
        for route in (window.PAGE_CAPABILITIES, window.PAGE_FAVORITES, window.PAGE_KNOWLEDGE, window.PAGE_TEAM_SPACES, window.PAGE_SETTINGS):
            assert window.show_product_page(route)
            capture(route)
        assert (state_path.read_bytes() if state_path.exists() else None) == saved_before
        manager.restore_saved_theme(reason="preview_finished")
        manager.stop()
        window.close()
        deadline = time.monotonic() + 5
        while not getattr(window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
            QTest.qWait(10)
        assert window._checkpoint_close_ready
        window.file_model.directoryLoaded.disconnect()
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        background_patch.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("light", "dark"), default="light")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    render(args.mode, args.scale, args.compact)

"""Verify the UI review against isolated data; optionally leave a native preview open."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]


def verify(dark=False, scale=1, compact=False, native=False, interactive=False):
    os.environ["QT_QPA_PLATFORM"] = "windows" if native else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    with tempfile.TemporaryDirectory(prefix="cowork-ui-review-") as temporary:
        os.environ.update(APPDATA=temporary, LOCALAPPDATA=temporary)
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: temporary
        from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QRect, Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import MainWindow, initialize_desktop_theme
        from ui_window_cleanup import close_test_window

        app = QApplication([])
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        initialize_desktop_theme(app)
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        with patch.object(MainWindow, "start_background_services", lambda self: None), \
                patch("core.config_manager.get_app_data_dir", return_value=temporary), \
                patch("core.config_manager.get_base_dir", return_value=temporary):
            window = MainWindow(config_manager=ConfigManager(), theme_manager=manager)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        window._model_onboarding_shown = True
        window.skill_manager.load_skills(load_mcp_tools=False)
        window.skill_manager_ready = True
        window.refresh_selected_skill_controls()
        window.resize(1000 if compact else 1440, 760 if compact else 900)
        window.show()
        state_path = Path(repository.store_path)
        saved_before = state_path.read_bytes() if state_path.exists() else None
        assert manager.apply_profile(builtin_theme("default", dark=dark), preview=True), manager.last_error
        output = ROOT / "docs/screenshots/ui-review-20261025"
        output.mkdir(parents=True, exist_ok=True)
        suffix = f"{'dark' if dark else 'light'}-{int(scale * 100)}{'-compact' if compact else ''}{'-windows' if native else ''}"
        captures = []
        display = {"logical_dpi": window.screen().logicalDotsPerInch(),
                   "device_pixel_ratio": window.devicePixelRatioF(),
                   "font_family": app.font().family(), "style": app.style().objectName()}

        def settle():
            deadline = time.monotonic() + .18
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            window.sync_context_drawer_layout()

        def capture(name, widget=None):
            settle()
            path = output / f"{name}-{suffix}.png"
            assert (widget or window).grab().save(str(path))
            captures.append(path.name)

        try:
            capture("home")
            window.home_page.home_options.setChecked(True)
            assert window.home_page.home_hints.isVisible()
            capture("home-options")
            window.home_page.home_options.setChecked(False)
            window.input_field.setPlainText("隔离验证草稿：保留任务要求和当前选择。")
            assert window.show_product_page(window.PAGE_FAVORITES)
            capture("favorites")
            assert window.show_favorite_editor(prefill={"name": "整理项目周报", "prompt": "整理本周进展、待办和风险。"})
            editor = window.product_pages["favorite_editor"]
            capture("favorite-editor")
            editor.run_options_toggle.setChecked(True)
            editor.schedule_attached_check.setChecked(True)
            editor.schedule_prompt_mode_combo.setCurrentIndex(1)
            editor.schedule_prompt_edit.setPlainText("汇总项目资料，生成周报文件。")
            settle()
            assert editor.settings_column.width() >= editor.task_column.width() - 4
            editor.scroll.ensureWidgetVisible(editor.schedule_card, 0, 0)
            capture("favorite-schedule")
            payload = editor.favorite_payload()
            assert payload["name"] == "整理项目周报" and payload["prompt"] == "整理本周进展、待办和风险。"
            assert payload["schedule"]["custom_prompt"] == "汇总项目资料，生成周报文件。"
            editor.scroll.verticalScrollBar().setValue(editor.scroll.verticalScrollBar().maximum())
            capture("favorite-delivery")
            # This fixture's unsaved form is deliberately discarded, never saved.
            window._close_favorite_editor()
            assert window.show_product_page(window.PAGE_CAPABILITIES)
            capability = window.product_pages[window.PAGE_CAPABILITIES]
            capture("capabilities")
            for section in ("mcp", "agents"):
                assert capability.select_section(section)
                capture("capabilities-" + section)
            assert window.show_product_page(window.PAGE_SETTINGS, "外观与阅读")
            capture("settings")
            assert window.show_conversation_page()
            assert window.input_field.toPlainText().startswith("隔离验证草稿")
            state = window.get_current_session()
            window.add_chat_bubble("User", "请检查这次 UI 调整。", animate=False)
            window.add_chat_bubble("Agent", "页面保持原有功能，常用任务按填写顺序排列，完整信息仍可展开。", animate=False)
            window.show_context_drawer(window.RIGHT_TAB_OBSERVABILITY)
            window.set_observability_section(window.OBS_SECTION_DETAILS)
            assert window.td_detail_splitter.isHidden()
            capture("details-empty")
            window.show_tool_details("ui-review-read", {"path": "示例.md"}, "已读取完整示例内容。", {"duration": .24})
            assert not window.td_detail_splitter.isHidden()
            assert window.td_empty_action.isHidden()
            capture("details")
            window.hide_context_drawer()
            state.failed_run_retry_context = {"source_run_id": "ui-review-failure", "source_user_message_id": "fixture-user"}
            state.run_failure_details = {"run_id": "ui-review-failure", "error": "隔离验证连接中断"}
            notice = window._show_retryable_run_failure(state, "连接中断。输入和已完成内容已保留，可重试继续。", "ui-review-failure")
            assert not notice.action_button.isHidden() and not notice.details_button.isHidden()
            settle()
            state.chat_scroll.ensureWidgetVisible(notice)
            capture("failure")
            popover = state.token_usage_label._popover
            full = "完整统计验证\n" + "缓存、输入和输出\n" * 100
            popover.set_detail_text(full)
            popover.show_for(state.token_usage_label)
            settle()
            host_bounds = QRect(window.mapToGlobal(QPoint()), window.size())
            assert host_bounds.contains(popover.geometry()), (host_bounds, popover.geometry())
            saved_clipboard = QMimeData()
            current_clipboard = app.clipboard().mimeData()
            if current_clipboard is not None:
                for mime in current_clipboard.formats():
                    saved_clipboard.setData(mime, current_clipboard.data(mime))
            popover.copy_button.click()
            assert app.clipboard().text() == full
            app.clipboard().setMimeData(saved_clipboard)
            popover.details_button.click()
            assert popover.details.toPlainText() == full
            capture("usage", popover)
            popover.hide()
            assert (state_path.read_bytes() if state_path.exists() else None) == saved_before
            if interactive:
                window.show_product_page(window.PAGE_FAVORITES)
                window.setWindowTitle("Cowork UI 验证（隔离数据）")
                print("Native UI preview ready", flush=True)
                app.exec()
        finally:
            manager.restore_saved_theme(reason="ui_review_finished")
            if not getattr(window, "_checkpoint_close_ready", False):
                close_test_window(window)
            else:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            manager.stop()
        report = {"platform": app.platformName(), "dark": dark, "qt_scale_factor": scale,
                  "display": display,
                  "compact": compact, "captures": captures, "checks": [
                      "favorite_payload_preserved", "full_width_schedule", "draft_preserved",
                      "empty_details_and_selected_details", "usage_within_window",
                      "full_usage_copy", "theme_preview_did_not_write_saved_state"]}
        (output / f"report-{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    verify(args.dark, args.scale, args.compact, args.native, args.interactive)

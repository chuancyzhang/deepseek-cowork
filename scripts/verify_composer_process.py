"""Exercise the shared composer and lazily restored process with isolated data."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]


def run(*, scale=1, compact=False, dark=False, native=False, interactive=False, output=None):
    os.environ["QT_QPA_PLATFORM"] = "windows" if native else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    output = Path(output or ROOT / "docs/screenshots/composer-process-20261025")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-composer-process-") as temporary:
        os.environ.update(APPDATA=temporary, LOCALAPPDATA=temporary)
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: temporary
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import MainWindow, initialize_desktop_theme
        from ui.primitives import ProductActionRow
        from ui_window_cleanup import close_test_window

        app = QApplication([])
        QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", "msyh.ttc")))
        QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", "consola.ttf")))
        initialize_desktop_theme(app)
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        with patch("core.config_manager.get_app_data_dir", return_value=temporary), \
                patch("core.config_manager.get_base_dir", return_value=temporary), \
                patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow(ConfigManager(), theme_manager=manager)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        window._model_onboarding_shown = True
        window.skill_manager_ready = True
        skills = [
            {"name": "report", "display_name": "周报整理", "description": "整理进展、行动项和来源。", "type": "ai_generated"},
            {"name": "research", "display_name": "资料核对", "description": "核对来源和引用，保留可检查的依据。", "type": "ai_generated"},
            {"name": "docs", "display_name": "文档检索", "description": "查找项目文档。", "source_format": "mcp_server"},
            {"name": "files", "display_name": "项目文件", "description": "读取当前项目文件。", "source_format": "mcp_server"},
        ]
        window._available_session_skills = lambda: skills
        window.resize(1020 if compact else 1440, 760 if compact else 900)
        window.show()
        saved_path = Path(repository.store_path)
        saved_before = saved_path.read_bytes() if saved_path.exists() else None
        assert manager.apply_profile(builtin_theme("default", dark=dark), preview=True), manager.last_error
        suffix = f"{'dark' if dark else 'light'}-{scale:g}x{'-compact' if compact else ''}{'-windows' if native else ''}"
        report = {"platform": app.platformName(), "qt_scale_factor": scale,
                  "device_pixel_ratio": window.devicePixelRatioF(), "logical_dpi": window.screen().logicalDotsPerInch(),
                  "captures": [], "checks": [], "tool_geometry": []}

        def settle():
            deadline = time.monotonic() + .2
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)

        def capture(name):
            settle()
            filename = f"{name}-{suffix}.png"
            assert window.grab().save(str(output / filename))
            report["captures"].append(filename)

        try:
            state = window.get_current_session()
            window._home_recent_records = [
                {"id": "source-one", "title": "产品调研周报", "updated_at": time.time(), "meta": {}},
                {"id": "source-two", "title": "整理本周进展", "updated_at": time.time(), "meta": {}},
            ]
            state.conversation_refs = [{"session_id": "source-one", "title": "产品调研周报"},
                                       {"session_id": "source-two", "title": "整理本周进展"}]
            state.selected_skill_names = ["docs", "files"]
            sample_file = Path(temporary, "项目资料摘要.md")
            sample_file.write_text("隔离验证附件", encoding="utf-8")
            window._set_prompt_files([str(sample_file)], state.session_id)
            window.refresh_conversation_reference_chips()
            window.refresh_selected_skill_controls()
            window._sync_home_presentation()
            capture("home-context")
            assert window.input_field.height() >= 104
            assert len(window.composer_context_tags.chips("references")) == 2
            assert len(window.composer_context_tags.chips("skills")) == 2
            report["checks"].append("shared_context_and_editing_space")
            window.input_field.setPlainText("保留这段草稿，切换后继续编辑。")
            window.show_prompt_tool_menu()
            menu = window.composer_action_popover
            row = next(row for row in menu.findChildren(ProductActionRow) if row.title_label.text() == "技能")
            menu._open_submenu(row)
            capture("skill-cascade")
            assert menu.isVisible() and menu._child_popover.isVisible()
            menu.close()
            report["checks"].append("cascade_preserved")
            window.show_product_page(window.PAGE_FAVORITES)
            window.show_conversation_page()
            assert window.input_field.toPlainText() == "保留这段草稿，切换后继续编辑。"
            report["checks"].append("draft_preserved")
            window.input_field.clear()
            state.conversation_refs = []
            state.selected_skill_names = []
            window._set_prompt_files([], state.session_id)
            window.refresh_conversation_reference_chips()
            window.refresh_selected_skill_controls()

            messages = [{"id": "user", "role": "user", "content": "请读取项目资料，整理本周进展，并保留来源。"}]
            events = []
            now = 1700000000.0
            for index in range(7):
                failed = index in (3, 4)
                text = ("我先核对项目里的调研记录和素材，再整理已完成内容与后续行动。" if index == 0 else
                        "两份资料已读取，接下来检查表格中的字段和遗漏。" if index == 2 else
                        "这次调用失败，已保留返回的原因；我将根据它继续处理。" if index == 4 else "")
                reasoning = "核对资料来源、日期和字段，保留能直接支持结论的内容。" if index in (1, 2, 5) else ""
                tool_name = "run_skill_script" if failed else "text_file_read"
                args = {"skill_name": "report", "script_name": "prepare.py"} if failed else {"path": f"资料/项目调研记录-{index + 1}.md"}
                meta = {"ui_turn_group_id": "fixture-turn", "ui_stage_id": f"stage-{index}", "ui_turn_id": "77", "ui_reply_kind": "stage"}
                messages.append({"id": f"assistant-{index}", "role": "assistant", "content": text, "reasoning_content": reasoning, "meta": meta,
                                 "tool_calls": [{"id": f"tool-{index}", "type": "function", "function": {"name": tool_name, "arguments": json.dumps(args)}}]})
                result = {"status": "error", "error": "参数缺少 call 子命令，尚未执行。请检查调用格式后重试。"} if failed else {"ok": True, "content": "已读取隔离验证资料。"}
                messages.append({"id": f"result-{index}", "role": "tool", "tool_call_id": f"tool-{index}", "content": json.dumps(result, ensure_ascii=False),
                                 "result_obj": result, "meta": {"duration": .3 + index / 10, "start_time": now + index * 3}})
                if reasoning and index != 5:
                    events.append({"kind": "thinking", "turn_id": "77", "group_id": "fixture-turn", "stage_id": f"stage-{index}",
                                   "text": reasoning, "started_at": now + index * 3, "finished_at": now + index * 3 + 1.2,
                                   "sequence": len(events) + 1, "status": "completed"})
            messages.append({"id": "final", "role": "assistant", "content": "已整理本周进展，并保留来源与待核实内容。", "meta": {
                "ui_turn_group_id": "fixture-turn", "ui_stage_id": "stage-final", "ui_turn_id": "77", "ui_reply_kind": "final"}})
            snapshot = copy.deepcopy(messages)

            def reload_history():
                window._handle_session_history_loaded({"ok": True, "session_id": state.session_id, "token": state.history_load_token,
                    "messages": messages, "conversation_meta": {"ui_timeline_v1": events, "history_save_revision": state.chat_save_revision},
                    "conversation_record": {"title": "历史过程排版验证", "status": "completed"}})
                deadline = time.monotonic() + 5
                while state.history_initial_render_completion is not None and time.monotonic() < deadline:
                    settle()
                assert state.history_initial_render_completion is None

            reload_history()
            capture("history-collapsed")
            summary = state.history_detail_summaries[0]
            summary.detail_button.setChecked(True)
            deadline = time.monotonic() + 8
            while summary.detail_loading and time.monotonic() < deadline:
                settle()
            assert not summary.detail_loading
            bar = state.chat_scroll.verticalScrollBar()
            bar.setValue(bar.minimum())
            capture("history-expanded")
            bar.setValue(bar.maximum() // 2)
            capture("history-failure")
            for card in state.tool_cards.values():
                state.chat_scroll.ensureWidgetVisible(card)
                settle()
                assert card.name_label.height() >= card.name_label.fontMetrics().height()
                assert card.main_row.geometry().bottom() < card.tool_context_label.geometry().top()
                if card.failed:
                    assert card.tool_context_label.geometry().bottom() < card.args_preview_label.geometry().top()
                    assert card.args_preview_label.height() >= card.args_preview_label.fontMetrics().height()
                assert card.icon_label.isHidden()
                report["tool_geometry"].append({"id": card.tool_id, "card": [card.width(), card.height()],
                    "row": [card.main_row.width(), card.main_row.height()],
                    "title": [card.name_label.width(), card.name_label.height()], "font_height": card.name_label.fontMetrics().height(),
                    "details": [card.view_btn.width(), card.view_btn.height()]})
            report["checks"].append("tool_text_and_failure_do_not_overlap")
            bubbles = [b for g in summary.detail_groups for b in getattr(g, "stage_bubbles", [])]
            for bubble in bubbles:
                if bubble.ui_stage_id == "stage-5":
                    assert bubble.think_toggle_btn.text() == "深度思考"
                elif getattr(bubble, "_thinking_text_length", 0):
                    assert "1.2 秒" in bubble.think_toggle_btn.text()
                else:
                    assert bubble.think_toggle_btn.isHidden()
            report["checks"].append("thinking_uses_only_known_durations")
            first = next(b for g in summary.detail_groups for b in getattr(g, "stage_bubbles", []) if getattr(b, "_thinking_text_length", 0))
            first.think_toggle_btn.setChecked(True)
            state.chat_scroll.ensureWidgetVisible(first)
            capture("history-thought")
            assert messages == snapshot
            assert state.messages == snapshot
            report["checks"].append("history_does_not_mutate_messages")
            summary.detail_button.setChecked(False)
            assert summary.body.isVisible()
            summary.detail_button.setChecked(True)
            assert not summary.detail_loading
            report["checks"].append("fold_keeps_final_and_materialized_details")
            target = state.tool_cards["tool-3"]
            target.view_btn.click()
            settle()
            assert window.current_selected_tool_id == "tool-3"
            assert "参数缺少" in window.td_result_edit.toPlainText()
            capture("history-tool-details")
            window.hide_context_drawer()
            report["checks"].append("tool_details_target_correct")
            reload_history()
            state.history_detail_summaries[0].detail_button.setChecked(True)
            deadline = time.monotonic() + 8
            while state.history_detail_summaries[0].detail_loading and time.monotonic() < deadline:
                settle()
            assert len(state.tool_cards) == 7
            report["checks"].append("history_reload_no_duplicate_tools")
            recovery_calls = []
            notice = window._show_conversation_notice(state, "连接中断，输入和已完成内容已保留。可重试继续。", "error",
                action_text="重试", action_callback=lambda: recovery_calls.append("retry"))
            notice.set_details(lambda: recovery_calls.append("details"))
            settle()
            state.chat_scroll.ensureWidgetVisible(notice)
            capture("conversation-recovery")
            notice.action_button.click()
            notice.details_button.click()
            assert recovery_calls == ["retry", "details"]
            window.add_system_toast("已导出周报，可在文件与交付物中打开。", "success", auto_close_ms=0)
            settle()
            toast = window._visible_system_toasts[-1]
            assert toast.geometry().bottom() < window.input_card.mapTo(window.main_container, QPoint()).y()
            capture("toast-chat")
            window.show_product_page(window.PAGE_FAVORITES)
            window._position_active_system_toast()
            capture("toast-page")
            assert toast.geometry().bottom() <= window.main_container.height()
            window._dismiss_system_toast(toast)
            window.show_conversation_page()
            report["checks"].append("notification_position_and_recovery_actions")
            assert (saved_path.read_bytes() if saved_path.exists() else None) == saved_before
            report["checks"].append("theme_preview_does_not_write_saved_state")
            if interactive:
                window.setWindowTitle("Cowork 输入与过程验证（隔离数据）")
                state.chat_scroll.verticalScrollBar().setValue(0)
                print("Native composer/process preview ready", flush=True)
                app.exec()
        finally:
            (output / f"report-{suffix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if sys.exc_info()[0] is not None:
                import traceback
                traceback.print_exc()
            window.save_chat_history(session_id=state.session_id)
            assert window.wait_for_chat_save_revision(state.session_id, state.chat_save_revision, timeout_ms=8000)
            settle()
            if not getattr(window, "_checkpoint_close_ready", False):
                close_test_window(window)
            manager.stop()
        (output / f"report-{suffix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output), "checks": report["checks"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--output")
    run(**vars(parser.parse_args()))

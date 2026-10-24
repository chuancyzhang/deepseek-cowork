"""Verify the real thought timeline and task-details drawer with isolated data."""
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


def run(scale):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    with tempfile.TemporaryDirectory(prefix="cowork-thinking-preview-") as temporary:
        os.environ["APPDATA"] = str(Path(temporary, "roaming"))
        os.environ["LOCALAPPDATA"] = str(Path(temporary, "local"))
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: temporary
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        from core.config_manager import ConfigManager
        from core.theme import DesignTokens, ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import AssistantTurnGroup, ChatBubble, MainWindow, ToolCallCard

        app = QApplication([])
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        saved = repository.load()
        output = ROOT / "docs/images/thinking-timeline"
        output.mkdir(parents=True, exist_ok=True)
        host = QWidget()
        host.setObjectName("ThinkingTimelinePreview")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(ChatBubble("User", "帮我核对这条新闻的来源，并说明哪些内容可以确认。"))
        group = AssistantTurnGroup("thinking-preview")
        layout.addWidget(group)
        thoughts = (
            "The source records include publication dates and original links.\n\n"
            "The report needs to keep direct quotations separate from interpretation, "
            "so the reader can inspect the evidence behind each statement.",
            "The first retrieval returned a network error. The failed call remains "
            "in the timeline while the next request checks the same source.",
            "The retrieved material contains two source records. The final response "
            "will distinguish confirmed information from points that still need checking.",
        )
        cards = []
        for index, text in enumerate(thoughts):
            bubble = ChatBubble("Agent", "", thinking=text, duration=(24, 12, 64)[index])
            group.add_stage(bubble)
            if index < 2:
                card = ToolCallCard("run_skill_script", {"skill_name": "news", "script_name": "search.py"}, f"preview-tool-{index}")
                result = {"status": "error", "error": "网络连接中断，请重试。"} if index == 0 else {"status": "success", "sources": 2}
                card.set_result(json.dumps(result, ensure_ascii=False), result)
                bubble.add_tool_card(card)
                cards.append(card)
            bubble.set_main_content(("我来核对相关报道的发布时间和原始来源。", "已保留失败记录，继续核对来源。", "已完成来源核对。可以确认的内容与仍需核实的部分已分别整理。\n\n- 两条材料均保留原始来源和发布时间。\n- 缺少直接出处的说法列为待核实。") [index], final=True)
        group.process_elapsed_seconds = 100
        group.finalize_non_result_stages(group.active_stage())
        group.finalize_process(group.active_stage())
        group.process_disclosure.setChecked(True)

        def settle():
            deadline = time.monotonic() + .18
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)

        def capture(name, width):
            host.setStyleSheet(f"QWidget#ThinkingTimelinePreview {{ background: {DesignTokens.bg_chat}; }}")
            host.setFixedWidth(width)
            host.show()
            settle()
            host.adjustSize()
            settle()
            host.adjustSize()
            if app.focusWidget():
                app.focusWidget().clearFocus()
            for bubble in group.stage_bubbles:
                for thought in bubble.timeline_events:
                    editor = getattr(thought, "_plain_edit", None)
                    if editor is not None and editor.isVisible():
                        cursor = editor.textCursor()
                        cursor.movePosition(cursor.MoveOperation.End)
                        assert editor.cursorRect(cursor).bottom() <= editor.viewport().height(), name
            assert host.grab().save(str(output / f"{name}-{scale:g}x.png"))
            for card in cards:
                assert card.view_btn.isVisible()
                assert card.main_row.geometry().right() < card.width() + 1
                assert card.name_label.width() > 45
            return host.grab().toImage()

        assert manager.apply_profile(builtin_theme("default"), preview=True), manager.last_error
        capture("timeline-light", 736)
        before = capture("timeline-light", 736)
        group.stage_bubbles[0].think_toggle_btn.click()
        capture("original-light", 736)
        capture("original-narrow", 380)
        last = group.active_stage()
        last.think_toggle_btn.click()
        last.set_thinking_state(True)
        last.update_thinking("\n\nAdditional streamed text is appended in place.")
        capture("streaming-narrow", 380)
        last.stop_thinking_timers()
        capture("stopped-narrow", 380)
        assert manager.apply_profile(builtin_theme("default", dark=True), preview=True), manager.last_error
        capture("original-dark", 380)
        assert manager.restore_saved_theme(reason="thinking_preview_cancel"), manager.last_error
        assert repository.load() == saved
        last.update_thinking(duration=64, is_final=True)
        last.think_toggle_btn.setChecked(False)
        group.stage_bubbles[0].think_toggle_btn.setChecked(False)
        after = capture("restored-light", 736)
        assert before == after, "Canceling theme preview must restore the same rendering"
        host.hide()

        # Exercise the unchanged signal -> real MainWindow drawer path.
        with patch.object(MainWindow, "start_background_services", lambda self: None), \
             patch("core.config_manager.get_app_data_dir", return_value=temporary), \
             patch("core.config_manager.get_base_dir", return_value=temporary):
            window = MainWindow(config_manager=ConfigManager(), theme_manager=manager)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        window._model_onboarding_shown = True
        window.resize(1320, 860)
        window.show()
        state = window.get_current_session()
        window.add_chat_bubble("User", "核对新闻来源", animate=False)
        layout.removeWidget(group)
        state.chat_layout.insertWidget(state.chat_layout.count() - 1, group)
        for card in cards:
            state.tool_cards[card.tool_id] = card
            card.clicked.connect(window.show_tool_details)
        settle()
        cards[0].view_btn.click()
        settle()
        window.sync_context_drawer_layout()
        assert window.current_selected_tool_id == cards[0].tool_id
        assert window._tool_detail_session_id == state.session_id
        assert window.td_result_edit.isVisible()
        assert "网络连接中断" in window.td_result_edit.toPlainText()
        assert window.grab().save(str(output / f"task-details-{scale:g}x.png"))
        window.hide_context_drawer()
        cards[1].setFocus()
        QTest.keyClick(cards[1], Qt.Key_Return)
        settle()
        assert window.current_selected_tool_id == cards[1].tool_id
        assert window.td_result_edit.isVisible()
        assert "sources" in window.td_result_edit.toPlainText()
        assert repository.load() == saved
        window.close()
        deadline = time.monotonic() + 5
        while not getattr(window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window._checkpoint_close_ready
        window.file_model.directoryLoaded.disconnect()
        window.deleteLater()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        manager.stop()
        print(f"Timeline, streaming, narrow layout, theme cancellation and task drawer verified: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1)
    run(parser.parse_args().scale)

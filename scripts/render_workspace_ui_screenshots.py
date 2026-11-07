"""Render isolated workspace fixtures without using personal data or model calls."""

from qa_support import prepare_qa_directory
QA_ROOT = prepare_qa_directory(__file__)
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def render(mode="light", scale=1, compact=False, focused=False, message_context=False, writing_pages=False, native=False, composer_context=False, composer_mode=False):
    os.environ["QT_QPA_PLATFORM"] = "windows" if native else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    with tempfile.TemporaryDirectory(prefix="cowork-workspace-preview-") as temporary:
        os.environ["APPDATA"] = str(Path(temporary, "roaming"))
        os.environ["LOCALAPPDATA"] = str(Path(temporary, "local"))
        import core.env_utils
        core.env_utils.get_app_data_dir = lambda: temporary
        from PySide6.QtCore import QCoreApplication, QEvent, Qt, QObject, QMimeData
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QWidget
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        from core.theme_presets import builtin_theme
        from main import MainWindow, initialize_desktop_theme

        app = QApplication([])
        if composer_context or composer_mode:
            initialize_desktop_theme(app)
        for font in ("msyh.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"], "Fonts", font)))
        repository = ThemeRepository(temporary)
        manager = ThemeRuntimeManager(app, repository)
        app.theme_manager = manager
        if composer_context or composer_mode:
            assert manager.apply_profile(builtin_theme("default", dark=mode == "dark"), preview=True), manager.last_error
        class WindowAudit(QObject):
            active = writing_pages or message_context or composer_context or composer_mode
            events = []
            def eventFilter(self, obj, event):
                if (self.active and event.type() == QEvent.Show and isinstance(obj, QWidget)
                        and obj.isWindow() and not isinstance(obj, MainWindow)
                        and not obj.testAttribute(Qt.WA_DontShowOnScreen)):
                    self.events.append((type(obj).__name__, obj.objectName()))
                return False
        audit = WindowAudit(app)
        app.installEventFilter(audit)
        background_patch = patch.object(MainWindow, "start_background_services", lambda self: None)
        background_patch.start()
        with patch("core.config_manager.get_app_data_dir", return_value=temporary), patch("core.config_manager.get_base_dir", return_value=temporary):
            config = ConfigManager()
            window = MainWindow(config_manager=config, theme_manager=manager)
        window._model_onboarding_shown = True
        window.skill_manager.load_skills(load_mcp_tools=False)
        window.skill_manager_ready = True
        window.refresh_selected_skill_controls()
        window.resize((760 if focused or composer_context or composer_mode else 1100) if compact else 1440, 760 if compact else 900)
        window.show()
        state_path = Path(repository.store_path)
        saved_before = state_path.read_bytes() if state_path.exists() else None
        assert manager.apply_profile(builtin_theme("default", dark=mode == "dark"), preview=True), manager.last_error
        output = QA_ROOT / "screenshots" / ("composer-mode" if composer_mode else "composer-context" if composer_context else "writing-pages" if writing_pages else "message-context" if message_context else "focused-workspace" if focused else "workspace-redesign")
        output.mkdir(parents=True, exist_ok=True)

        def settle():
            QTest.qWait(180)
            window.sync_context_drawer_layout()
            QTest.qWait(100)

        def capture(name):
            if composer_mode and not name.startswith("composer-mode"):
                return
            if composer_context and not name.startswith("composer-context"):
                return
            if writing_pages and not name.startswith("writing-"):
                return
            if message_context and not name.startswith("message-context"):
                return
            if focused and not message_context and name not in {"home-empty", "home", "conversation", "conversation-hover", "conversation-files", "conversation-failure", "capabilities", "settings", "settings-search"}:
                return
            settle()
            if message_context and name.startswith("message-context"):
                scroll = window.get_current_session().chat_scroll.verticalScrollBar()
                scroll.setValue(scroll.minimum())
                QTest.qWait(40)
            destination = output / f"{name}-{mode}-{int(scale * 100)}{'-compact' if compact else ''}{'-windows' if native else ''}.png"
            assert window.grab().save(str(destination))
            print(destination, flush=True)

        def finish():
            assert (state_path.read_bytes() if state_path.exists() else None) == saved_before
            manager.restore_saved_theme(reason="preview_finished")
            manager.stop()
            window.close()
            deadline = time.monotonic() + 5
            while not getattr(window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert window._checkpoint_close_ready
            window.file_model.directoryLoaded.disconnect()
            window.deleteLater()
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            if writing_pages:
                QTest.qWait(500)
            background_patch.stop()

        if composer_mode:
            import json
            from core.clarify_mode import GRILL_MODE_ACTIVE, GRILL_MODE_ARMED, GRILL_MODE_DISABLED

            state = window.get_current_session()
            chip = window.grill_mode_badge
            assert chip.isHidden()
            window.input_field.setPlainText("请先帮我梳理方案中的关键决策。")
            window.show_composer_mode_picker()
            choices = window.composer_mode_choices
            QTest.mouseClick(choices.viewport(), Qt.LeftButton,
                             pos=choices.visualItemRect(choices.item(1)).center())
            window.composer_mode_popover.close()
            capture("composer-mode-home")
            assert state.grill_mode_state == GRILL_MODE_ARMED
            assert chip.isVisible() and chip.close_btn.isVisible()
            suffix = f"{mode}-{int(scale * 100)}{'-compact' if compact else ''}{'-windows' if native else ''}"
            assert window.input_row.grab().save(str(output / f"composer-mode-input-{suffix}.png"))
            draft = window.input_field.toPlainText()
            attachment = Path(temporary, "方案.txt")
            attachment.write_text("胶囊取消时保留的附件。", encoding="utf-8")
            window._set_prompt_files([str(attachment)])
            QTest.mouseClick(chip.close_btn, Qt.LeftButton)
            assert chip.isHidden() and state.grill_mode_state == GRILL_MODE_DISABLED
            assert window.input_field.toPlainText() == draft
            assert window._current_prompt_files() == [str(attachment)]
            window.toggle_grill_mode()
            window._set_prompt_files([])
            state.messages = [{"id": "mode-preview-user", "role": "user", "content": "先讨论方案"}]
            window.add_chat_bubble("User", "先讨论方案", source_message_id="mode-preview-user", animate=False)
            window.normalize_session_ui(state)
            capture("composer-mode-conversation")
            assert not window._home_presentation_active and not window.home_scroll.isVisible()
            for control in (chip, window.model_select_btn, window.action_btn):
                assert window.input_card.rect().contains(control.mapTo(window.input_card, control.rect().topLeft()))
                assert window.input_card.rect().contains(control.mapTo(window.input_card, control.rect().bottomRight()))
            assert not chip.geometry().intersects(window.agent_picker_btn.geometry())
            state.grill_mode_state = GRILL_MODE_ACTIVE
            window.refresh_grill_mode_controls(state.session_id)
            assert chip.isVisible() and chip.close_btn.isHidden() and not chip.main_btn.isEnabled()
            capture("composer-mode-active")
            state.grill_mode_state = GRILL_MODE_ARMED
            window.refresh_grill_mode_controls(state.session_id)
            manager.apply_profile(builtin_theme("default", dark=mode != "dark"), preview=True)
            settle()
            manager.apply_profile(builtin_theme("default", dark=mode == "dark"), preview=True)
            settle()
            assert chip.isVisible() and chip.close_btn.isVisible()
            assert window.input_field.toPlainText() == draft
            window.create_new_session(make_current=True)
            settle()
            assert chip.isHidden()
            window.set_current_session(state.session_id)
            settle()
            assert chip.isVisible() and window.input_field.toPlainText() == draft
            assert not audit.events, audit.events
            print(json.dumps({"platform": app.platformName(), "qt_scale_factor": scale,
                              "device_pixel_ratio": window.devicePixelRatioF(),
                              "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
                              "native_window_events": audit.events,
                              "theme_preview_persisted": False}, ensure_ascii=False), flush=True)
            audit.active = False
            finish()
            return

        if focused:
            capture("home-empty")
            assert not window.workspace_title_label.isVisible()
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
        attachments = []
        if message_context:
            for name in ("产品介绍.pdf", "访谈纪要.md", "gaic_kuaidi100_agent_symbiosis_from_html_final.pptx"):
                path = Path(temporary, name)
                path.write_text("附件布局示例，不用于文件内容预览。", encoding="utf-8")
                attachments.append({"path": str(path)})
            current.messages[0]["meta"] = {"conversation_refs": [{"session_id": "reference-preview", "title": "产品讨论与需求梳理", "snapshot_path": str(Path(temporary, "引用快照.md")), "captured_at": "2026-10-24 09:00"}]}
        user_bubble = window.add_chat_bubble("User", current.messages[0]["content"], source_message_id="preview-user", animate=False, attachments=attachments)
        answer_bubble = window.add_chat_bubble("Agent", current.messages[1]["content"], source_message_id="preview-answer", animate=False)
        window.normalize_session_ui(current)
        assert window.input_field.toPlainText().startswith("保留的草稿")
        assert window.input_row.parentWidget() is window.conversation_column
        capture("conversation")
        if composer_context:
            import json
            current.conversation_refs = [{"session_id": "reference-preview", "title": "上次交割问题讨论"}]
            current.knowledge_refs = [
                {"title": "交割部交割答疑手册", "source": "weknora", "knowledge_id": "manual"},
                {"title": "交割业务办理流程与材料清单（修订版）", "source": "tencent-docs", "item_id": "process"},
            ]
            window._available_session_skills = lambda: [{"name": "document-analysis", "display_name": "文档分析", "type": "ai_generated"}]
            current.selected_skill_names = ["document-analysis"]
            attachment = Path(temporary, "交割办理问题清单.txt")
            attachment.write_text("资料标签布局示例。", encoding="utf-8")
            window._set_prompt_files([str(attachment)])
            window.refresh_selected_skill_controls()
            window.refresh_context_badges()
            window.input_field.setPlainText("请根据手册整理交割办理的常见问题。")
            capture("composer-context")
            suffix = f"{mode}-{int(scale * 100)}{'-compact' if compact else ''}{'-windows' if native else ''}"
            assert window.input_row.grab().save(str(output / f"composer-context-input-{suffix}.png"))
            for chip in window.composer_context_tags.chips("knowledge"):
                assert chip.type_label.text() == "资料"
                assert chip.rect().contains(chip.close_btn.geometry())
            strip = window.composer_context_tags
            strip.verticalScrollBar().setValue(strip.verticalScrollBar().maximum())
            settle()
            last = strip.chips("knowledge")[-1]
            assert strip.viewport().rect().contains(last.close_btn.mapTo(strip.viewport(), last.close_btn.rect().center()))
            strip.verticalScrollBar().setValue(0)
            draft = window.input_field.toPlainText()
            window.composer_context_tags.chips("knowledge")[0].main_btn.click()
            capture("composer-context-manage")
            popover = window.knowledge_reference_popover
            assert popover.isVisible()
            assert window.rect().contains(popover.geometry())
            popover.cancel_button.click()
            manager.apply_profile(builtin_theme("default", dark=mode != "dark"), preview=True)
            settle()
            manager.apply_profile(builtin_theme("default", dark=mode == "dark"), preview=True)
            settle()
            assert window.input_field.toPlainText() == draft
            assert len(window.composer_context_tags.chips("knowledge")) == 2
            assert not audit.events, audit.events
            audit.active = False
            report = {"platform": app.platformName(), "qt_scale_factor": scale,
                      "device_pixel_ratio": window.devicePixelRatioF(),
                      "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
                      "native_window_events": audit.events, "theme_preview_persisted": False}
            print(json.dumps(report, ensure_ascii=False), flush=True)
            finish()
            return
        if writing_pages:
            import json
            from PySide6.QtGui import QInputMethodEvent
            browser = window.browser_workbench
            window.show_context_drawer(window.RIGHT_TAB_FILES)
            window.set_file_navigator_visible(False, reason="writing_page_qa")
            if browser.active_tab() is None or browser.active_tab().kind != "new":
                browser.new_tab()
            capture("writing-start")
            browser.new_page()
            page = browser.panes[browser.active_tab().id]
            page.title.setText("产品讨论纪要")
            page.editor.setMarkdown("## 本次结论\n\n以 **任务为中心**，保留用户的输入和已完成成果。\n\n## 下一步\n\n1. 验证工作区展开与恢复。\n2. 检查保存后重新打开。\n3. 整理待确认的问题。\n\n*讨论记录将随任务继续完善。*")
            page.editor.setFocus()
            event = QInputMethodEvent()
            event.setCommitString("中文输入验证。")
            app.sendEvent(page.editor, event)
            assert "中文输入验证" in page.editor.toPlainText()
            page.editor.undo()
            capture("writing-split")
            original_width = window.right_sidebar.width()
            window.toggle_deliverable_preview_expanded()
            capture("writing-expanded")
            assert window.right_sidebar.width() > window.main_container.width() * .9
            browser.chat_button.click()
            capture("writing-conversation")
            assert window.input_field.isVisible()
            browser.activate(page.tab.id)
            destination = Path(page.workspace) / "产品讨论纪要.md"
            page.save(target=str(destination))
            deadline = time.monotonic() + 8
            while page.worker is not None and time.monotonic() < deadline:
                QTest.qWait(20)
            assert page.worker is None and destination.exists() and not page.dirty
            capture("writing-saved")
            window.toggle_deliverable_preview_expanded()
            settle()
            assert window.right_sidebar.width() == original_width
            tab = browser.open_web(current.session_id, path=str(ROOT / "test/fixtures/browser-wide-page.html"))
            browser.activate(tab.id)
            web = browser.panes[tab.id]
            deadline = time.monotonic() + 8
            while not web.document_ready and time.monotonic() < deadline:
                QTest.qWait(20)
            assert web.document_ready
            capture("writing-browser-compact")
            window.toggle_deliverable_preview_expanded()
            capture("writing-browser-expanded")
            window.toggle_deliverable_preview_expanded()
            browser.close_tab(tab.id)
            browser.activate(page.tab.id)
            content_before_theme = page.content()
            manager.apply_profile(builtin_theme("default", dark=mode != "dark"), preview=True)
            settle()
            assert page.content() == content_before_theme and not page.dirty
            manager.apply_profile(builtin_theme("default", dark=mode == "dark"), preview=True)
            settle()
            assert page.content() == content_before_theme and not page.dirty
            audit.active = False
            report = {"platform": app.platformName(), "qt_scale_factor": scale,
                      "device_pixel_ratio": window.devicePixelRatioF(),
                      "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
                      "native_window_events": audit.events, "ime_commit": "passed",
                      "theme_preview_persisted": False}
            (output / f"validation-{mode}-{scale}{'-windows' if native else ''}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            assert not audit.events, audit.events
        if message_context:
            capture("message-context")
            user_bubble.begin_inline_edit()
            pasted = QMimeData()
            pasted.setText("产品讨论的补充资料。\n" * 100)
            user_bubble.user_content_edit.insertFromMimeData(pasted)
            assert len(user_bubble.edit_attachment_paths) == len(attachments) + 1
            assert not current.prompt_files
            manager.apply_profile(builtin_theme("default", dark=mode != "dark"), preview=True)
            settle()
            manager.apply_profile(builtin_theme("default", dark=mode == "dark"), preview=True)
            capture("message-context-edit")
            from PySide6.QtWidgets import QPushButton
            assert all(user_bubble.edit_controls.rect().contains(button.geometry())
                       for button in user_bubble.edit_controls.findChildren(QPushButton))
            user_bubble.cancel_inline_edit()
            assert window.input_field.toPlainText().startswith("保留的草稿")
            audit.active = False
            import json
            report = {"platform": app.platformName(), "qt_scale_factor": scale,
                      "device_pixel_ratio": window.devicePixelRatioF(),
                      "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
                      "native_window_events": audit.events}
            (output / f"validation-{mode}-{scale}{'-windows' if native else ''}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            assert not audit.events, audit.events
        if focused:
            QTest.mouseMove(user_bubble, user_bubble.rect().center())
            capture("conversation-hover")
            QTest.mouseMove(window.top_bar_widget)
            paths = []
            for name in ("产品发布方案.md", "发布文案.md", "演示流程.md", "验收清单.md", "实施计划.md", "数据来源.md"):
                path = Path(window._workspace_dir_for_state(current), name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# 示例交付文件\n\n用于验证文件列表与预览入口。\n", encoding="utf-8")
                paths.append(str(path))
            answer_bubble.deliverable_cards.set_files(paths, window._workspace_dir_for_state(current))
            settle()
            current.chat_scroll.ensureWidgetVisible(answer_bubble.deliverable_cards)
            capture("conversation-files")
            answer_bubble.deliverable_cards.hide()
            current.failed_run_retry_context = {"source_run_id": "preview-failure", "source_user_message_id": "preview-user"}
            current.run_failure_details = {"run_id": "preview-failure", "message": "连接中断（隔离截图示例）"}
            notice = window._show_retryable_run_failure(current, "连接在生成过程中中断。当前输入和已完成的内容已保留，可重试继续。", "preview-failure")
            settle()
            current.chat_scroll.ensureWidgetVisible(notice)
            capture("conversation-failure")
            notice.hide()
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
        routes = (window.PAGE_CAPABILITIES, window.PAGE_SETTINGS) if focused else (window.PAGE_CAPABILITIES, window.PAGE_FAVORITES, window.PAGE_KNOWLEDGE, window.PAGE_TEAM_SPACES, window.PAGE_SETTINGS)
        for route in routes:
            assert window.show_product_page(route)
            capture(route)
            if focused and route == window.PAGE_SETTINGS:
                page = window.product_pages[route]
                page.settings_search.setText("字体")
                capture("settings-search")
                QTest.keyClick(page.settings_search, Qt.Key_Return)
                assert page._page_labels[page._active_settings_row] == "外观与阅读"
                page.settings_search.clear()
        finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("light", "dark"), default="light")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--focused", action="store_true", help="Capture only the home, conversation, capabilities and settings changes")
    parser.add_argument("--message-context", action="store_true", help="Capture sent attachments and conversation references inside the user message")
    parser.add_argument("--writing-pages", action="store_true", help="Verify writing pages, expanded conversation tabs and compact browser controls")
    parser.add_argument("--native", action="store_true", help="Use real Windows widgets instead of offscreen rendering")
    parser.add_argument("--composer-context", action="store_true", help="Verify typed composer tags and knowledge selection without external services")
    parser.add_argument("--composer-mode", action="store_true", help="Verify the removable mode capsule, draft isolation and toolbar layout")
    args = parser.parse_args()
    render(args.mode, args.scale, args.compact, args.focused or args.message_context, args.message_context, args.writing_pages, args.native, args.composer_context, args.composer_mode)

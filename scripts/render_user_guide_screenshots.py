import os
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_SCALE_FACTOR", "1")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix="cowork-user-guide-"))
os.environ["APPDATA"] = str(TEMP_ROOT / "appdata")
APP_DATA_DIR = TEMP_ROOT / "appdata" / "DeepSeekCowork"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
(APP_DATA_DIR / "config.json").write_text("{}", encoding="utf-8")

from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

import main


OUTPUT_DIR = Path(os.environ.get("COWORK_SCREENSHOT_OUTPUT_DIR") or (ROOT / "images" / "user-guide"))
SCREENSHOT_SCOPE = str(os.environ.get("COWORK_SCREENSHOT_SCOPE") or "").strip().lower()


def process_events(delay_ms=120):
    QApplication.processEvents()
    QTest.qWait(delay_ms)
    QApplication.processEvents()


def save_widget(widget, filename, delay_ms=120):
    widget.show()
    widget.raise_()
    process_events(delay_ms)
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError(f"Unable to capture screenshot: {filename}")
    output_path = OUTPUT_DIR / filename
    if not pixmap.save(str(output_path), "PNG"):
        raise RuntimeError(f"Unable to save screenshot: {output_path}")


def save_window_with_popups(window, filename, delay_ms=120):
    process_events(delay_ms)
    screen = window.screen() or QApplication.primaryScreen()
    geometry = window.frameGeometry()
    pixmap = screen.grabWindow(0, geometry.x(), geometry.y(), geometry.width(), geometry.height())
    output_path = OUTPUT_DIR / filename
    if pixmap.isNull() or not pixmap.save(str(output_path), "PNG"):
        raise RuntimeError(f"Unable to capture screenshot with popups: {output_path}")


def render_assistant_turn_screens(window):
    state = window.get_current_session()
    window._retire_session_empty_state(state, reason="user_guide_assistant_turn")
    window.add_chat_bubble(
        "User",
        "请检查工作区和现有测试，再给出最终修改说明。",
        animate=False,
        source_message_id="guide-turn-user",
    )
    turn_group = main.AssistantTurnGroup("guide-turn-group")
    state.chat_layout.insertWidget(state.chat_layout.count() - 1, turn_group)
    stage = window._create_agent_chat_bubble(state)
    window._connect_chat_bubble_actions(stage, state)
    turn_group.add_stage(stage, "guide-turn-group:stage-1")
    stage.update_thinking("先分析任务目标，再检查工作区文件。")
    tool = main.ToolCallCard("run_command", {"command": "python -m pytest"}, "guide-turn-tool")
    stage.add_tool_card(tool)
    tool.set_result("测试通过")
    stage.update_thinking(duration=10.2, is_final=True)
    stage.set_message_actions_enabled(False)
    stage.set_main_content("先完成环境与测试检查。", final=True)
    stage.think_toggle_btn.setChecked(True)
    final = window._create_agent_chat_bubble(state)
    window._connect_chat_bubble_actions(final, state)
    turn_group.add_stage(final, "guide-turn-group:stage-2")
    final.update_thinking("根据检查结果整理最终答复。")
    final.update_thinking(duration=4.1, is_final=True)
    final.set_source_message_id("guide-turn-final")
    final.set_main_content("现有测试已经通过。我已按验证结果整理最终修改说明。", final=True)
    save_widget(window, "37-thinking-expanded.png", 220)

    stage.think_toggle_btn.setChecked(False)
    final.set_message_actions_enabled(False)
    window.add_turn_guidance_inline(
        {"id": "guide-timeline-demo", "content": "先验证现有测试，再继续修改界面。"},
        status="applied",
    )
    followup_group = main.AssistantTurnGroup("guide-followup-group")
    state.chat_layout.insertWidget(state.chat_layout.count() - 1, followup_group)
    followup = window._create_agent_chat_bubble(state)
    window._connect_chat_bubble_actions(followup, state)
    followup_group.add_stage(followup, "guide-followup-group:stage-1")
    followup.update_thinking("测试通过，继续整理最终结果。")
    followup.update_thinking(duration=4.1, is_final=True)
    followup.set_source_message_id("guide-followup-final")
    followup.set_main_content("现有测试已经通过。我已按验证结果整理最终修改说明。", final=True)
    save_widget(window, "38-guidance-timeline.png", 220)


def select_settings_page(dialog, label):
    for row in range(dialog.nav_list.count()):
        if dialog.nav_list.item(row).text() == label:
            dialog.nav_list.setCurrentRow(row)
            process_events()
            return
    raise RuntimeError(f"Settings page not found: {label}")


def verify_drawer_layout(window, width, height):
    window.resize(width, height)
    window.sync_context_drawer_layout()
    process_events(80)
    drawer_rect = window.right_sidebar.geometry()
    if drawer_rect.right() > window.main_container.width() or drawer_rect.width() < main.DesignTokens.drawer_min_width:
        raise RuntimeError(f"Invalid drawer geometry at {width}x{height}: {drawer_rect}")
    input_origin = window.input_card.mapTo(window.main_container, QPoint(0, 0))
    input_right = input_origin.x() + window.input_card.width()
    if input_right > drawer_rect.x() - window.context_drawer_gap:
        raise RuntimeError(
            f"Composer overlaps drawer at {width}x{height}: input_right={input_right}, drawer_x={drawer_rect.x()}"
        )


def quiet_show_event(window, event):
    QMainWindow.showEvent(window, event)


def main_run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    workspace = TEMP_ROOT / "Linear Demo Project"
    workspace.mkdir(parents=True, exist_ok=True)
    html_path = workspace / "weekly-brief.html"
    html_path.write_text(
        """<!doctype html><html><head><meta charset='utf-8'><style>
        body{font-family:Segoe UI,Microsoft YaHei,sans-serif;margin:0;background:#f7f7f8;color:#202124}
        main{max-width:960px;margin:40px auto;background:white;padding:42px;border:1px solid #e6e6e9;border-radius:12px}
        h1{font-size:34px;margin:0 0 10px}p{color:#5f6269}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:30px}
        article{background:#f4f4f5;padding:18px;border-radius:8px}strong{color:#5e6ad2;font-size:24px;display:block}
        </style></head><body><main><h1>本周项目简报</h1><p>将任务进度、关键结果和下周行动集中在一页中。</p>
        <section class='grid'><article><strong>12</strong>完成任务</article><article><strong>4</strong>新增交付物</article><article><strong>92%</strong>按期完成</article></section>
        </main></body></html>""",
        encoding="utf-8",
    )
    (workspace / "meeting-notes.md").write_text(
        "# 项目会议记录\n\n- 完成本周报告\n- 整理交付物\n- 确认下周计划\n",
        encoding="utf-8",
    )
    pptx_path = workspace / "quarterly-review.pptx"
    pptx_path.write_bytes(b"demo-pptx")
    pdf_path = workspace / "project-summary.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% demo")

    app = QApplication.instance() or QApplication([])
    main.initialize_desktop_theme(app)
    original_show_event = main.MainWindow.showEvent
    main.MainWindow.showEvent = quiet_show_event
    window = None
    try:
        window = main.MainWindow()
        window.resize(1280, 720)
        if SCREENSHOT_SCOPE == "skill-capture":
            skill_analysis = main.ConversationSkillEvidenceDialog(
                {
                    "confidence": "high",
                    "task_goal": {
                        "text": "把周报整理流程转成可重复执行的能力。",
                        "source_message_ids": ["demo-user"],
                    },
                    "outcome": {
                        "text": "已完成数据收集、结构整理和结果校验。",
                        "source_message_ids": ["demo-agent"],
                    },
                    "reusable_patterns": [
                        {
                            "text": "先统一收集进展，再按结果、风险和下周行动组织内容。",
                            "source_message_ids": ["demo-user", "demo-agent"],
                        }
                    ],
                    "missing_evidence": [],
                    "privacy_findings": [{"kind": "workspace_path"}],
                    "resource_candidates": [
                        {
                            "id": "weekly-report-reference",
                            "kind": "reference",
                            "description": "保存稳定的周报栏目定义",
                            "source_message_ids": ["demo-agent"],
                        }
                    ],
                },
                [],
                parent=window,
            )
            save_widget(skill_analysis, "s32-skill-capture.png", 200)
            skill_analysis.resize(620, 560)
            save_widget(skill_analysis, "s32-skill-capture-small.png", 200)
            skill_analysis.hide()
            return
        if SCREENSHOT_SCOPE == "theme-acceptance":
            from unittest.mock import patch

            from core.theme import ThemeRuntimeManager, default_design_tokens
            from core.theme_service import ThemeRepository

            repository = ThemeRepository(str(APP_DATA_DIR))
            theme_manager = ThemeRuntimeManager(app, repository)
            app.theme_manager = theme_manager
            window.theme_manager = theme_manager
            theme_manager.themeChanged.connect(window._apply_runtime_theme)
            theme_manager.previewStateChanged.connect(window._on_theme_preview_state)
            repository.write_preview(
                name="深夜验收",
                overrides={
                    "font_scale": 1.05,
                    "density": "compact",
                    "tokens": {
                        "primary": "#8b93ff",
                        "bg_app": "#101116",
                        "bg_main": "#15171d",
                        "bg_sidebar": "#12141a",
                        "bg_chat": "#15171d",
                        "text_primary": "#eceef3",
                        "text_secondary": "#b7bcc8",
                        "text_tertiary": "#858c9b",
                        "composer_bg": "#181b22",
                        "composer_text": "#eceef3",
                        "right_sidebar_bg": "#171920",
                        "right_sidebar_text": "#eceef3",
                        "management_bg": "#101116",
                        "management_panel_bg": "#181b22",
                        "overlay_bg": "#181b22",
                        "overlay_text": "#eceef3",
                        "preview_shell_bg": "#181b22",
                        "preview_shell_text": "#eceef3",
                        "border": "#343946",
                        "border_subtle": "#292d36",
                        "separator": "#292d36",
                        "sidebar_border": "#292d36",
                        "chat_border": "#292d36",
                        "composer_border": "#343946",
                        "right_sidebar_border": "#292d36",
                    },
                },
                default_tokens=default_design_tokens(),
                session_id="theme-acceptance",
            )
            with patch(
                "core.theme.QFontDatabase.families",
                return_value=["Microsoft YaHei UI", "Consolas"],
            ):
                if not theme_manager.apply_repository_state(reason="screenshot_acceptance"):
                    raise RuntimeError(theme_manager.last_error)
            window.add_chat_bubble(
                "User",
                "请展示左右侧栏、聊天区、工具卡和输入区的完整主题覆盖。",
                animate=False,
                source_message_id="theme-acceptance-user",
            )
            state = window.get_current_session()
            group = main.AssistantTurnGroup("theme-acceptance-group")
            state.chat_layout.insertWidget(state.chat_layout.count() - 1, group)
            bubble = window._create_agent_chat_bubble(state)
            group.add_stage(bubble, "theme-acceptance-stage")
            bubble.update_thinking("正在检查主题绑定、Markdown CSS 与自绘控件。")
            tool = main.ToolCallCard(
                "validate_ui_theme",
                {"areas": ["sidebar", "conversation", "composer", "drawer"]},
                "theme-acceptance-tool",
            )
            bubble.add_tool_card(tool)
            tool.set_result("全部区域通过")
            bubble.update_thinking(duration=2.4, is_final=True)
            bubble.set_source_message_id("theme-acceptance-assistant")
            bubble.set_main_content(
                "## 全界面主题已应用\n\n- 左右侧栏与聊天画布\n- 输入区、工具卡和 Markdown\n- 固定安全恢复条",
                final=True,
            )
            window.show_context_drawer(window.RIGHT_TAB_OBSERVABILITY)
            window.show()
            process_events(180)
            scale_label = str(os.environ.get("QT_SCALE_FACTOR") or "1").replace(".", "_")
            for width, height in ((1280, 720), (1440, 900), (1920, 1080)):
                window.resize(width, height)
                process_events(120)
                window.sync_context_drawer_layout()
                process_events(80)
                verify_drawer_layout(window, width, height)
                save_widget(
                    window,
                    f"theme-acceptance-{width}x{height}-{scale_label}x.png",
                    220,
                )
            return
        if SCREENSHOT_SCOPE == "model-settings":
            window.open_settings("模型与服务")
            settings = window.product_pages[window.PAGE_SETTINGS]
            settings._automatic_update_check_started = True
            save_widget(window, "06-model-service-settings.png")
            model_empty = main.ModelEditDialog("openai", parent=settings)
            model_empty.model_name_input.setText("gpt-5.6")
            save_widget(model_empty, "07-add-model.png")
            model_empty.hide()
            model_filled = main.ModelEditDialog(
                "openai",
                {
                    "display_name": "GPT-5.6 Sol",
                    "model_name": "gpt-5.6",
                    "api_protocol": "responses",
                    "supports_vision": True,
                    "thinking_enabled": True,
                    "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
                    "reasoning_effort": "medium",
                },
                parent=settings,
            )
            save_widget(model_filled, "08-model-configuration.png")
            model_filled.hide()
            return
        if SCREENSHOT_SCOPE == "assistant-turn":
            render_assistant_turn_screens(window)
            return
        save_widget(window, "04-home-screen.png", 250)
        model_style = window.model_select_btn.styleSheet()
        window.model_select_btn.setStyleSheet(main.apple_tool_button_style(True))
        save_widget(window, "09-switch-model.png")
        window.model_select_btn.setStyleSheet(model_style)
        window.show_model_selector_popover()
        if getattr(window, "model_selector_popover", None) is not None:
            save_window_with_popups(window, "33-model-popover.png")
            window.model_selector_popover.close()
            process_events(80)
        window.show_prompt_tool_menu()
        save_window_with_popups(window, "34-composer-add-popover.png", 200)
        window.composer_action_popover.close()
        project_add_style = window.sidebar_add_project_btn.styleSheet()
        window.sidebar_add_project_btn.setStyleSheet(main.apple_sidebar_icon_button_style(True))
        save_widget(window, "12-add-project.png")
        window.sidebar_add_project_btn.setStyleSheet(project_add_style)

        window.open_settings("模型与服务")
        settings = window.product_pages[window.PAGE_SETTINGS]
        settings._automatic_update_check_started = True
        save_widget(window, "05-open-settings.png")
        save_widget(window, "06-model-service-settings.png")

        model_empty = main.ModelEditDialog("openai", parent=settings)
        save_widget(model_empty, "07-add-model.png")
        model_empty.hide()
        model_filled = main.ModelEditDialog(
            "openai",
            {
                "display_name": "DeepSeek V4 Pro",
                "model_name": "deepseek-v4-pro",
                "api_protocol": "chat_completions",
                "supports_vision": True,
                "thinking_enabled": True,
                "reasoning_efforts": ["low", "medium", "high"],
            },
            parent=settings,
        )
        save_widget(model_filled, "08-model-configuration.png")
        model_filled.hide()
        select_settings_page(settings, "更新")
        save_widget(window, "10-update-settings.png")
        select_settings_page(settings, "个性与记忆")
        save_widget(window, "25-memory-center.png")
        window.show_conversation_page()

        window.skill_manager.load_skills()
        window.skill_manager_ready = True
        window.open_session_skill_picker()
        if getattr(window, "session_skill_popover", None) is not None:
            save_window_with_popups(window, "35-ability-popover.png")
            window.session_skill_popover.close()
        window.open_skills_center()
        skills = window.product_pages[window.PAGE_CAPABILITIES]
        save_widget(window, "23-skills-center.png")
        if skills._all_skills:
            window.show_capability_detail(skills._all_skills[0])
            save_widget(window, "29-capability-workbench.png")
        window.show_conversation_page()

        window.open_automation_center()
        save_widget(window, "24-automation-center.png")
        window.show_automation_task_editor()
        save_widget(window, "32-automation-task-editor.png")
        window.handle_product_back()
        window.show_conversation_page()

        agent_style = window.agent_picker_btn.styleSheet()
        window.agent_picker_btn.setStyleSheet(main.apple_button_style("selected", radius=7))
        save_widget(window, "26-agent-center.png")
        window.agent_picker_btn.setStyleSheet(agent_style)

        skill_analysis = main.ConversationSkillEvidenceDialog(
            {
                "confidence": "high",
                "task_goal": {
                    "text": "把周报整理流程转成可重复执行的能力。",
                    "source_message_ids": ["demo-user"],
                },
                "outcome": {
                    "text": "已完成数据收集、结构整理和结果校验。",
                    "source_message_ids": ["demo-agent"],
                },
                "reusable_patterns": [
                    {
                        "text": "先统一收集进展，再按结果、风险和下周行动组织内容。",
                        "source_message_ids": ["demo-user", "demo-agent"],
                    }
                ],
                "missing_evidence": [],
                "privacy_findings": [{"kind": "workspace_path"}],
                "resource_candidates": [
                    {
                        "id": "weekly-report-reference",
                        "kind": "reference",
                        "description": "保存稳定的周报栏目定义",
                        "source_message_ids": ["demo-agent"],
                    }
                ],
            },
            [],
            parent=window,
        )
        save_widget(skill_analysis, "31-skill-capture-analysis.png")
        skill_analysis.hide()

        ppt_agent = main.PptAgentModeDialog(str(workspace), parent=window)
        save_widget(ppt_agent, "27-ppt-agent.png")
        ppt_agent.hide()

        user_bubble = window.add_chat_bubble(
            "User",
            "请整理本周项目进展，并生成一份适合汇报的简报。",
            animate=False,
            source_message_id="guide-user-message",
        )
        window.add_chat_bubble(
            "Agent",
            "已整理任务进度、关键结果和下周行动。你可以继续让我生成 HTML 工作稿，或直接调整内容结构。",
            animate=False,
        )
        process_events(200)
        save_widget(window, "11-direct-chat.png")
        if user_bubble is not None:
            user_bubble.begin_inline_edit()
            save_widget(user_bubble, "36-history-message-edit.png", 200)
            user_bubble.cancel_inline_edit()
        state = window.get_current_session()
        turn_group = main.AssistantTurnGroup("guide-turn-group")
        state.chat_layout.insertWidget(state.chat_layout.count() - 1, turn_group)
        thinking_bubble = window._create_agent_chat_bubble(state)
        window._connect_chat_bubble_actions(thinking_bubble, state)
        turn_group.add_stage(thinking_bubble, "guide-turn-group:stage-1")
        if thinking_bubble is not None:
            thinking_bubble.update_thinking("先分析任务目标，再检查工作区文件。")
            demo_tool = main.ToolCallCard("run_command", {"command": "python -m unittest"}, "guide-demo-tool")
            thinking_bubble.add_tool_card(demo_tool)
            demo_tool.set_result("测试通过")
            thinking_bubble.update_thinking(duration=10.2, is_final=True)
            thinking_bubble.set_message_actions_enabled(False)
            thinking_bubble.set_main_content("先完成环境与测试检查。", final=True)
            thinking_bubble.think_toggle_btn.setChecked(True)

            final_bubble = window._create_agent_chat_bubble(state)
            window._connect_chat_bubble_actions(final_bubble, state)
            turn_group.add_stage(final_bubble, "guide-turn-group:stage-2")
            final_bubble.update_thinking("根据检查结果整理最终答复。")
            final_bubble.update_thinking(duration=4.1, is_final=True)
            final_bubble.set_source_message_id("guide-final-message")
            final_bubble.set_main_content("现有测试已经通过。我已按验证结果整理最终修改说明。", final=True)
            save_widget(window, "37-thinking-expanded.png")
            thinking_bubble.think_toggle_btn.setChecked(False)
            final_bubble.set_message_actions_enabled(False)

            window.add_turn_guidance_inline(
                {"id": "guide-timeline-demo", "content": "先验证现有测试，再继续修改界面。"},
                status="applied",
            )
            followup_group = main.AssistantTurnGroup("guide-followup-group")
            state.chat_layout.insertWidget(state.chat_layout.count() - 1, followup_group)
            followup_bubble = window._create_agent_chat_bubble(state)
            window._connect_chat_bubble_actions(followup_bubble, state)
            followup_group.add_stage(followup_bubble, "guide-followup-group:stage-1")
            followup_bubble.update_thinking("测试通过，继续整理最终结果。")
            followup_bubble.update_thinking(duration=4.1, is_final=True)
            followup_bubble.set_source_message_id("guide-followup-final")
            followup_bubble.set_main_content(
                "现有测试已经通过。我已按验证结果整理最终修改说明。",
                final=True,
            )
            save_widget(window, "38-guidance-timeline.png")

        clipboard_image = QImage(240, 140, QImage.Format_ARGB32)
        clipboard_image.fill(Qt.GlobalColor.lightGray)
        window._add_clipboard_image(clipboard_image)
        process_events(120)
        save_widget(window, "13-paste-image.png")
        window._clear_prompt_files(window.current_session_id)

        window.load_workspace(
            str(workspace),
            refresh_sidebar=False,
            remember_workspace=False,
            persist_default=False,
            bind_session=True,
        )
        process_events(200)
        save_widget(window, "14-project-workspace.png")
        window.add_chat_bubble(
            "Agent",
            f"HTML 工作稿已生成：{html_path.name}\n\n已按 16:9 汇报节奏整理，可以在右侧预览并继续转换。",
            animate=False,
        )
        window.chat_storage.register_deliverable(
            str(workspace),
            str(html_path),
            conversation_id=window.current_session_id,
            source="generated",
        )
        window.chat_storage.register_deliverable(
            str(workspace), str(pptx_path), conversation_id=window.current_session_id, source="converted"
        )
        window.chat_storage.register_deliverable(
            str(workspace), str(pdf_path), conversation_id=window.current_session_id, source="converted"
        )
        process_events(200)
        save_widget(window, "15-generate-html-example.png")

        window.show_context_drawer(window.RIGHT_TAB_FILES)
        window.set_file_workspace_section(window.FILE_SECTION_ALL, refresh=True, user_initiated=True)
        process_events(500)
        save_widget(window, "16-open-deliverables.png")
        window.set_file_workspace_section(window.FILE_SECTION_DELIVERABLES, refresh=True, user_initiated=True)
        process_events(250)
        save_widget(window, "17-deliverables-panel.png")
        window.add_system_toast("PPTX 已生成，可在交付物中打开。", "success", auto_close_ms=0)
        window.add_system_toast("资源管理器已打开并定位到文件。", "info", auto_close_ms=0)
        process_events(120)
        save_widget(window, "30-system-feedback.png")
        for toast in list(window._visible_system_toasts):
            window._dismiss_system_toast(toast)

        window.show_file_workspace_detail_view(origin="browse")
        window.select_deliverable(str(html_path), render_html=True)
        process_events(1200)
        save_widget(window, "18-open-html-preview.png")
        save_widget(window, "20-generate-pptx.png")
        tool_id = "guide-python-tool"
        window.add_tool_card(
            {
                "id": tool_id,
                "name": "run_python_code",
                "args": {
                    "code": "from pathlib import Path\n\nfiles = list(Path('.').glob('*.md'))\nprint({'files': len(files)})",
                    "timeout": 30,
                },
                "meta": {"start_time": 1783783680, "duration": 0.24},
            },
            session_id=window.current_session_id,
            animate=False,
        )
        window.update_tool_card(
            {
                "id": tool_id,
                "result": '{"files": 1, "status": "ok"}',
                "result_obj": {"files": 1, "status": "ok"},
                "meta": {"duration": 0.24},
            },
            session_id=window.current_session_id,
        )
        window.show_context_drawer(window.RIGHT_TAB_OBSERVABILITY)
        card = window.get_current_session().tool_cards[tool_id]
        window.show_tool_details(tool_id, card.args, card.result, meta=card.meta, switch_tab=True)
        process_events(180)
        save_widget(window, "28-task-observability.png")
        for width, height in ((1280, 720), (1440, 900), (1920, 1080)):
            verify_drawer_layout(window, width, height)
            save_widget(window, f"38-responsive-{width}x{height}.png", 80)
    finally:
        main.MainWindow.showEvent = original_show_event
        if window is not None:
            worker = getattr(window, "chat_save_worker", None)
            if worker is not None and worker.isRunning():
                worker.stop_worker(timeout_ms=3000)
            window.hide()
        app.quit()


if __name__ == "__main__":
    main_run()

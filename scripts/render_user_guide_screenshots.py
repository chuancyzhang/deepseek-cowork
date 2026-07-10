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
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

import main


OUTPUT_DIR = Path(os.environ.get("COWORK_SCREENSHOT_OUTPUT_DIR") or (ROOT / "images" / "user-guide"))


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

    app = QApplication.instance() or QApplication([])
    main.initialize_desktop_theme(app)
    original_show_event = main.MainWindow.showEvent
    main.MainWindow.showEvent = quiet_show_event
    window = None
    try:
        window = main.MainWindow()
        window.resize(1280, 720)
        save_widget(window, "04-home-screen.png", 250)
        model_style = window.model_select_btn.styleSheet()
        window.model_select_btn.setStyleSheet(main.apple_tool_button_style(True))
        save_widget(window, "09-switch-model.png")
        window.model_select_btn.setStyleSheet(model_style)
        project_add_style = window.sidebar_add_project_btn.styleSheet()
        window.sidebar_add_project_btn.setStyleSheet(main.apple_sidebar_icon_button_style(True))
        save_widget(window, "12-add-project.png")
        window.sidebar_add_project_btn.setStyleSheet(project_add_style)

        settings = main.SettingsDialog(window.config_manager, parent=window, initial_page_label="模型与服务")
        settings._automatic_update_check_started = True
        settings.resize(1040, 700)
        save_widget(settings, "05-open-settings.png")
        save_widget(settings, "06-model-service-settings.png")

        model_empty = main.ModelEditDialog("openai", parent=settings)
        save_widget(model_empty, "07-add-model.png")
        model_empty.hide()
        model_filled = main.ModelEditDialog(
            "openai",
            {
                "display_name": "DeepSeek V4 Pro",
                "model_name": "deepseek-v4-pro",
                "supports_vision": True,
                "thinking_enabled": True,
                "reasoning_efforts": ["low", "medium", "high"],
            },
            parent=settings,
        )
        save_widget(model_filled, "08-model-configuration.png")
        model_filled.hide()
        select_settings_page(settings, "更新")
        save_widget(settings, "10-update-settings.png")
        settings.hide()

        skills = main.SkillsCenterDialog(window.skill_manager, window.config_manager, parent=window)
        save_widget(skills, "23-skills-center.png")
        if skills._all_skills:
            workbench = main.CapabilityWorkbenchDialog(
                skills._all_skills[0], window.skill_manager, window.config_manager, parent=window
            )
            save_widget(workbench, "29-capability-workbench.png")
            workbench.hide()
        skills.hide()

        automation = main.AutomationDialog(window.config_manager, parent=window)
        save_widget(automation, "24-automation-center.png")
        automation.hide()

        memory = main.MemoryCenterDialog(str(APP_DATA_DIR), str(workspace), parent=window)
        save_widget(memory, "25-memory-center.png")
        memory.hide()

        agents = main.AgentModuleDialog(agent_profiles=[], parent=window)
        save_widget(agents, "26-agent-center.png")
        agents.hide()

        ppt_agent = main.PptAgentModeDialog(str(workspace), parent=window)
        save_widget(ppt_agent, "27-ppt-agent.png")
        ppt_agent.hide()

        window.add_chat_bubble("User", "请整理本周项目进展，并生成一份适合汇报的简报。", animate=False)
        window.add_chat_bubble(
            "Agent",
            "已整理任务进度、关键结果和下周行动。你可以继续让我生成 HTML 工作稿，或直接调整内容结构。",
            animate=False,
        )
        process_events(200)
        save_widget(window, "11-direct-chat.png")

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
        process_events(200)
        save_widget(window, "15-generate-html-example.png")

        window.show_context_drawer(window.RIGHT_TAB_FILES)
        window.set_file_workspace_section(window.FILE_SECTION_ALL, refresh=True)
        process_events(500)
        save_widget(window, "16-open-deliverables.png")
        window.set_file_workspace_section(window.FILE_SECTION_DELIVERABLES, refresh=True)
        process_events(250)
        save_widget(window, "17-deliverables-panel.png")

        window.show_file_workspace_detail_view(origin="browse")
        window.select_deliverable(str(html_path), render_html=True)
        process_events(1200)
        save_widget(window, "18-open-html-preview.png")
        save_widget(window, "20-generate-pptx.png")
        window.show_context_drawer(window.RIGHT_TAB_OBSERVABILITY)
        process_events(180)
        save_widget(window, "28-task-observability.png")
        for width, height in ((1280, 720), (1440, 900), (1920, 1080)):
            verify_drawer_layout(window, width, height)
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

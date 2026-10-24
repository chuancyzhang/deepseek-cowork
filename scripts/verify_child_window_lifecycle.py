"""Audit real page/picker lifecycles for unintended native top-level windows."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"


def run(output, screenshots=False, history_screenshots=False):
    with tempfile.TemporaryDirectory(prefix="cowork-window-audit-") as temp:
        os.environ.update(APPDATA=temp, LOCALAPPDATA=temp)
        data = Path(temp, "DeepSeekCowork")
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"download_sources": {
            kind: {"selected": "audit-custom", "custom": [{
                "id": "audit-custom", "name": "验证源", "url": "https://example.com/" + kind + "/"}]}
            for kind in ("python", "node")
        }}), encoding="utf-8")
        from PySide6.QtCore import QEvent, QObject, Qt
        from PySide6.QtWidgets import QApplication, QWidget, QLabel
        from PySide6.QtGui import QFontDatabase
        from core.config_manager import ConfigManager
        from core.theme import ThemeRuntimeManager
        from core.theme_service import ThemeRepository
        from core.theme_presets import builtin_theme
        from main import (MainWindow, SessionSkillPickerPopover, CapabilityWorkbenchDialog,
                          FavoriteEditorPage, McpServerEditDialog, InlineInteractionCard,
                          SubAgentEventTile, IM_PROVIDER_SPECS)
        from core.conversation_agents import builtin_agent_profiles
        from ui.primitives import ProductActionRow, ProductPopover
        from ui_window_cleanup import close_test_window

        app = QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts", name)))
        config = ConfigManager()
        manager = ThemeRuntimeManager(app, ThemeRepository(str(data)))
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="child_window_audit")

        class Tracker(QObject):
            def __init__(self):
                super().__init__()
                self.stage = "construct"
                self.events = []
                self.allowed = None

            def eventFilter(self, obj, event):
                if (event.type() == QEvent.Show and isinstance(obj, QWidget)
                        and obj.isWindow() and obj is not self.allowed):
                    self.events.append({"stage": self.stage, "class": type(obj).__name__,
                        "object": obj.objectName(), "window_type": int(obj.windowType()),
                        "text": obj.text() if isinstance(obj, QLabel) else "",
                        "parent": type(obj.parentWidget()).__name__ if obj.parentWidget() else None,
                        "stack": [f"{Path(frame.filename).name}:{frame.lineno} {frame.name}: {frame.line}"
                                  for frame in traceback.extract_stack()[:-1]
                                  if str(ROOT).casefold() in str(Path(frame.filename).resolve()).casefold()][-8:]})
                return False

        tracker = Tracker()
        app.installEventFilter(tracker)
        window = MainWindow(config_manager=config, theme_manager=manager)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        tracker.allowed = window
        window.resize(1360, 860)
        window.show()

        def pump():
            deadline = time.monotonic() + .12
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)

        stages = []

        def stage(name, action):
            tracker.stage = name
            before = len(tracker.events)
            action()
            pump()
            stages.append({"stage": name, "unexpected_windows": len(tracker.events) - before})
            capture_name = {"composer-专家": "expert-picker", "composer-对话": "conversation-picker",
                            "library-tencent-docs": "tencent-library", "library-lexiang": "lexiang-library",
                            "history-expert-final": "history-expert", "history-details": "history-details",
                            "history-theme-dark-narrow": "history-dark-narrow"}.get(name)
            capture = screenshots or (history_screenshots and name.startswith("history-"))
            if capture and capture_name:
                output.parent.mkdir(parents=True, exist_ok=True)
                scale = os.environ.get("QT_SCALE_FACTOR", "1")
                assert window.grab().save(str(output.parent / f"{capture_name}-{scale}x.png"))

        try:
            pump()
            expert = builtin_agent_profiles()[0]
            state = window.get_current_session()
            fixture = [
                {"id": "audit-user", "role": "user", "content": "请整理页面",
                 "meta": {"conversation_agent": expert}},
                {"id": "audit-final", "role": "assistant", "content": "页面已整理。",
                 "meta": {"ui_reply_kind": "final", "ui_conversation_agent": expert}},
            ]
            state.messages = fixture
            stage("history-expert-final", lambda: window._render_session_history_spans(
                state, [{"start": 0, "end": 1}, {"start": 1, "end": 2}]))
            other = window.create_new_session(make_current=False)
            stage("conversation-switch-away", lambda: window.set_current_session(other))
            stage("conversation-switch-back", lambda: window.set_current_session(state.session_id))
            fixture[1]["reasoning_content"] = "检查页面标题与正文的层次。"
            fixture.extend([
                {"id": "audit-main-user", "role": "user", "content": "回到普通对话",
                 "meta": {"conversation_agent": {}}},
                {"id": "audit-main-final", "role": "assistant", "content": "普通助手回复。",
                 "meta": {"ui_reply_kind": "final", "ui_conversation_agent": {}}},
            ])

            def reload_history():
                window._handle_session_history_loaded({"ok": True, "session_id": state.session_id,
                    "token": state.history_load_token, "messages": fixture,
                    "conversation_meta": {"conversation_agent": expert},
                    "conversation_record": {"title": "专家与普通对话验证", "status": "completed"}})
                deadline = time.monotonic() + 5
                while state.history_initial_render_completion is not None and time.monotonic() < deadline:
                    pump()
                assert state.history_initial_render_completion is None

            stage("history-reload", reload_history)
            summary, ordinary = state.history_detail_summaries
            stage("history-details", lambda: summary.detail_button.setChecked(True))
            assert summary.detail_groups and not summary.detail_loading
            for widget, expected in ((summary, "HTML创作"), (ordinary, "Cowork")):
                assert [label.text() for label in widget.findChildren(QLabel, "ConversationAgentName")
                        if label.isVisibleTo(widget)] == [expected]
            stage("history-details-collapse", lambda: summary.detail_button.setChecked(False))
            stage("expert-selection-clear", window.select_conversation_agent)
            assert summary.identity_header.name_label.text() == "HTML创作"
            stage("expert-selection-restore", lambda: window.select_conversation_agent(expert))
            assert ordinary.identity_header.name_label.text() == "Cowork"

            def dark_narrow():
                window.resize(980, 760)
                assert manager.apply_profile(builtin_theme("default", dark=True), preview=True)

            stage("history-theme-dark-narrow", dark_narrow)
            stage("history-theme-restore", lambda: manager.restore_saved_theme(reason="history_audit"))
            window.resize(1360, 860)
            stage("user-message-attachment", lambda: window.add_chat_bubble(
                "User", "参考文件", attachments=[{"path": "example.txt"}], animate=False))
            stage("user-message-attachment-only", lambda: window.add_chat_bubble(
                "User", "", attachments=[{"path": "example.txt"}], animate=False))
            subtask_host = QWidget(window)
            stage("subtask-reply", lambda: window._render_subagent_conversation_item(
                {"type": "assistant", "content": "子任务已完成"}, subtask_host))
            window.skill_manager_ready = True
            window._available_session_skills = lambda: [
                {"name": "report", "display_name": "报告", "type": "ai_generated"},
                {"name": "docs", "display_name": "文档连接器", "source_format": "mcp_server"},
            ]
            window.chat_storage.upsert_conversation("reference", title="验证用历史对话")
            stage("composer-plus", window.show_prompt_tool_menu)
            menu = window.composer_action_popover
            for title in ("模式", "技能", "专家", "连接器", "对话", "技能", "专家"):
                row = next(row for row in menu.findChildren(ProductActionRow) if row.title_label.text() == title)
                stage("composer-" + title, lambda row=row: menu._open_submenu(row))
            menu.close()
            stage("skill-picker-transactional", lambda: SessionSkillPickerPopover(
                window._available_session_skills(), parent=window).show_for(window.tool_menu_btn))
            for popover in window.findChildren(ProductPopover):
                if popover.isVisible():
                    popover.close()
            stage("model-picker", window.show_model_selector_popover)
            for popover in window.findChildren(ProductPopover):
                if popover.isVisible():
                    popover.close()
            for route in ("knowledge", "capabilities", "favorites", "settings", "projects", "team_spaces", "conversation"):
                stage("route-" + route, lambda route=route: window.show_product_page(route))
                if route == "knowledge":
                    page = window.product_pages[route]
                    for index in range(page.sources.count()):
                        source = page.sources.itemData(index)
                        stage("library-" + source, lambda index=index: page.sources.setCurrentIndex(index))
            widgets = []
            skill_manager = MagicMock()
            skill_manager.is_skill_editable.return_value = True
            skill_manager.list_skill_files.return_value = {"ok": True, "files": []}
            skill_manager.get_skill_config_status.return_value = {
                "missing_required": [], "config_errors": [], "complete": True}
            skill_manager.get_tool_record.return_value = None
            stage("editable-skill-files", lambda: widgets.append(CapabilityWorkbenchDialog(
                {"name": "audit-skill", "display_name": "验证技能", "tools": [], "script_entries": []},
                skill_manager, config, parent=window)))
            stage("favorite-schedule", lambda: widgets.append(FavoriteEditorPage(favorite={
                "id": "audit-favorite", "name": "验证计划", "prompt": "验证内容",
                "schedule": {"schedule_type": "daily", "time_of_day": "09:00"}}, parent=window)))
            stage("mcp-new", lambda: widgets.append(McpServerEditDialog(parent=window)))
            for kind in ("text", "choice", "multi_choice"):
                stage("inline-" + kind, lambda kind=kind: widgets.append(InlineInteractionCard({
                    "kind": kind, "message": "验证输入", "allow_free_text": True,
                    "options": [{"label": "示例", "value": "example"}]}, parent=window)))
            stage("subagent-details", lambda: widgets.append(SubAgentEventTile(
                {"status": "completed", "content": "验证详情"}, parent=window)))
            for widget in widgets:
                widget.deleteLater()
            pump()
        finally:
            app.removeEventFilter(tracker)
            for page in getattr(window, "product_pages", {}).values():
                for source_page in getattr(page, "pages", {}).values():
                    source_page.upload_timer.stop()
                    source_page.jobs.pool.shutdown(wait=True)
            close_test_window(window)
            manager.stop()
            report = {"platform": app.platformName(), "im_provider_count": len(IM_PROVIDER_SPECS),
                      "stages": stages, "unexpected": tracker.events}
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"platform": report["platform"], "stages": len(stages),
                              "unexpected_windows": len(tracker.events), "report": str(output)}, ensure_ascii=True))
        return not tracker.events


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots/child-window-audit/report.json")
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--history-screenshots", action="store_true")
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output, args.screenshots, args.history_screenshots) else 1)

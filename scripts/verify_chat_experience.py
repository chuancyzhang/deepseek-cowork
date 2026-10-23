"""Render real chat surfaces and measure navigation with isolated local data."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")
os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"


def run(*, references_only=False):
    with tempfile.TemporaryDirectory(prefix="cowork-chat-qa-") as temp:
        os.environ.update(APPDATA=temp, LOCALAPPDATA=temp)
        data = Path(temp, "DeepSeekCowork")
        data.mkdir()
        (data / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QMimeData, Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from core.config_manager import ConfigManager
        from core.conversation_render import build_conversation_render_spans
        from core.theme import ThemeRuntimeManager
        from core.theme_presets import builtin_theme
        from core.theme_service import ThemeRepository
        from main import MainWindow
        from ui.primitives import ProductActionRow
        app = QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts", name)))
        config = ConfigManager()
        manager = ThemeRuntimeManager(app, ThemeRepository(str(data)))
        app.theme_manager = manager
        assert manager.apply_repository_state(reason="chat_qa")
        window = MainWindow(config_manager=config, theme_manager=manager)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        state = window.get_current_session()
        window.clear_chat_layout(state.chat_layout)
        state.empty_state = None
        state.messages = [
            {"id": "u1", "role": "user", "content": "请整理 AI 发展规划，突出实施路径与关键里程碑。"},
            {"id": "a1", "role": "assistant", "content": "已整理为可执行的三阶段计划。\n\n### 实施路径\n\n1. **夯实基础**：梳理资料、数据与现有能力。\n2. **试点验证**：选择高价值场景，记录效果与反馈。\n3. **逐步推广**：复用已验证的方案，完善运营机制。\n\n查看 [规划说明](https://example.com/plan)，或继续补充具体要求。"},
        ]
        state.render_items = build_conversation_render_spans(state.messages)
        state.displayed_render_count = len(state.render_items)
        state.history_loaded = True
        window._render_session_history_spans(state, state.render_items)
        window._sync_home_presentation()
        window.workspace_title_label.setText("AI 发展规划")
        scale = os.environ.get("QT_SCALE_FACTOR", "1")
        out = ROOT / "docs/screenshots" / ("conversation-references" if references_only else "chat-experience")
        out.mkdir(parents=True, exist_ok=True)
        window.show()

        def capture(name, width=1360, height=860):
            window.resize(width, height)
            QTest.qWait(250)
            window.sync_conversation_widths()
            QTest.qWait(120)
            assert window.grab().save(str(out / f"{name}-{scale}x.png"))
            assert window.input_card.width() <= window.conversation_column.width()
            for button in (window.action_btn, window.model_select_btn):
                assert button.mapTo(window.input_card, button.rect().bottomRight()).x() < window.input_card.width()

        def shutdown():
            window.close()
            deadline = time.monotonic() + 5
            while not getattr(window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert getattr(window, "_checkpoint_close_ready", False)
            window.deleteLater()
            app.processEvents()
            manager.stop()

        if references_only:
            workspace = window._workspace_dir_for_state(state)
            for sid, title, source, project, status in (
                ("reference-plan", "AI 发展规划 · 会议纪要", "chat", workspace, "active"),
                ("reference-research", "用户访谈与需求整理", "chat", workspace, "active"),
                ("reference-roadmap", "技术路线与实施计划", "project", "D:/projects/产品规划", "active"),
                ("reference-archive", "试点项目复盘", "chat", "", "archived"),
            ):
                window.chat_storage.save_conversation(sid, [{"id": sid + "-u", "role": "user", "content": title}],
                    title=title, status=status, meta={"workspace_source": source, "workspace_dir": project})
            draft = "请参考之前的讨论，补充各阶段的验收标准。"
            window.input_field.setPlainText(draft)
            window.resize(860, 720)
            QTest.qWait(250)
            window.show_prompt_tool_menu()
            capture("plus-menu", 860, 720)
            row = next(row for row in window.composer_action_popover.findChildren(ProductActionRow)
                       if row.title_label.text() == "对话")
            QTest.mouseClick(row.title_label, Qt.LeftButton)
            app.processEvents()
            picker = window.conversation_mention_popover
            capture("picker", 860, 720)
            assert window.rect().contains(picker.geometry())
            picker.search.setText("规划")
            capture("search", 860, 720)
            assert picker.items.count() == 2
            picker.search.setText("不存在的关键词")
            capture("empty", 860, 720)
            assert picker.empty.isVisible()
            picker.search.setText("会议纪要")
            QTest.keyClick(picker.search, Qt.Key_Return)
            capture("added", 860, 720)
            assert state.conversation_refs == [{"session_id": "reference-plan", "title": "AI 发展规划 · 会议纪要"}]
            assert window.input_field.toPlainText() == draft
            window.resize(740, 620)
            QTest.qWait(150)
            window.show_conversation_reference_picker()
            capture("minimum", 740, 620)
            assert window.rect().contains(window.conversation_mention_popover.geometry())
            saved = manager.repository.load()
            assert manager.apply_profile(builtin_theme("default", dark=True), preview=True, reason="reference_qa")
            capture("dark", 740, 620)
            assert manager.restore_saved_theme(reason="reference_qa_cancel")
            assert manager.repository.load() == saved
            shutdown()
            print(f"Conversation reference screenshots verified at {scale}x")
            return

        capture("conversation")
        mime = QMimeData()
        mime.setText("待分析的长文本，完整保留。\n" * 400)
        window.input_field.insertFromMimeData(mime)
        window.input_field.setPlainText("请结合附件，补充各阶段的验收标准。")
        window.skill_manager_ready = True
        window._available_session_skills = lambda: [
            {"name": "data-analysis", "type": "ai_generated", "display_name": "数据分析", "description": "分析数据"},
            {"name": "document", "type": "ai_generated", "display_name": "文档", "description": "整理文档"},
            {"name": "pdf", "source_format": "mcp_server", "display_name": "文档连接器", "description": "读取已连接的文档"},
        ]
        window.set_session_selected_skills(["data-analysis", "document", "pdf"])
        QTest.qWait(3300)
        capture("attachment")
        window._open_conversation_search()
        window.conversation_search.input.setText("验证")
        capture("search")
        capture("small", 860, 720)
        capture("minimum", 740, 620)
        window.show_prompt_tool_menu()
        capture("plus-menu", 860, 720)
        window.composer_action_popover.close()
        window.show_composer_mode_picker()
        capture("mode-menu", 860, 720)
        window.composer_mode_popover.close()
        window.open_session_skill_picker()
        capture("skills", 860, 720)
        window.session_skill_popover.close()
        window.open_session_skill_picker(category="connectors")
        capture("connectors", 860, 720)
        window.session_skill_popover.close()
        window._show_composer_permission()
        capture("permissions", 860, 720)
        window.permission_popover.close()
        saved = manager.repository.load()
        assert manager.apply_profile(builtin_theme("default", dark=True), preview=True, reason="chat_qa")
        capture("dark-small", 860, 720)
        assert manager.restore_saved_theme(reason="chat_qa_cancel")
        assert manager.repository.load() == saved
        window._close_conversation_search()
        window.resize(1360, 860)
        measurements = {}
        for name, action in (("settings_first", window.open_settings), ("back", window.show_conversation_page),
                             ("settings_repeat", window.open_settings), ("knowledge_first", lambda: window.show_product_page("knowledge")),
                             ("settings_after_library", window.open_settings), ("knowledge_repeat", lambda: window.show_product_page("knowledge"))):
            start = time.perf_counter()
            assert action()
            app.processEvents()
            measurements[name] = round((time.perf_counter() - start) * 1000, 1)
        window.show_conversation_page()
        original = window.current_session_id
        window.create_new_session()
        start = time.perf_counter()
        window.activate_session(original)
        app.processEvents()
        measurements["session_switch"] = round((time.perf_counter() - start) * 1000, 1)
        print(json.dumps(measurements, ensure_ascii=False))
        (out / f"navigation-{scale}x.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--references-only", action="store_true")
    run(references_only=parser.parse_args().references_only)

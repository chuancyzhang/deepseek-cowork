"""Capture actual capability pages using isolated, synthetic configuration."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots/capability-cards")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-capability-cards-") as root:
        os.environ["APPDATA"] = str(Path(root) / "appdata")
        config_dir = Path(os.environ["APPDATA"]) / "DeepSeekCowork"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QPushButton
        from core.theme import ThemeRuntimeManager, DesignTokens, default_design_tokens
        from core.theme_service import ThemeRepository
        from ui.capability_widgets import CapabilityCard
        from ui.skillhub_widgets import ClampedText, store_columns
        import main

        app = QApplication.instance() or QApplication([])
        for filename in ("msyh.ttc", "consola.ttf", "segoeui.ttf", "seguisym.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
        main.initialize_desktop_theme(app)
        theme = ThemeRuntimeManager(app, ThemeRepository(str(Path(root) / "themes")))
        app.theme_manager = theme
        theme.apply_repository_state(reason="capability_cards_qa")
        window = main.MainWindow(theme_manager=theme)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        scale = os.environ.get("QT_SCALE_FACTOR", "1")
        samples = [
            ("web-search", "网页搜索", "搜索最新网页信息，提取正文并整理来源。", "search_browse"),
            ("browser-automation", "浏览器自动化", "读取登录后的网页，操作页面与填写表单。", "search_browse"),
            ("document-reader", "本地文档读取", "读取 Word、PDF、Excel 和 PowerPoint 文件。", "docs_knowledge"),
            ("showdoc", "ShowDoc 接口文档", "搜索项目接口，阅读 API 文档与使用说明。", "docs_knowledge"),
            ("data-analysis", "数据分析", "整理表格中的数据，找出变化并生成图表。", "data_analysis"),
            ("slides", "演示文稿", "将资料整理成结构清晰、便于讲述的演示文稿。", "content_creation"),
            ("research", "研究报告", "比较公开资料，汇总事实与需要继续核实的问题。", "financial_research"),
        ]
        skills = [dict(name=key, display_name=name, source_type="bundled_plugin", enabled=index < 2,
                       config_fields=[], presentation=dict(category=category, short_name=name,
                       summary=description, examples=["示例一", "示例二"], access_note="使用用户指定资料。"))
                  for index, (key, name, description, category) in enumerate(samples)]
        manager = Mock()
        manager.skill_records = {}
        manager.get_all_skills.return_value = skills
        manager.is_skill_editable.return_value = False
        window.skill_manager = manager
        window.skill_manager_ready = True
        window.config_manager.set_agent_profiles([
            dict(id="qa-review", name="代码审查助手", description="检查代码改动，指出缺陷、边界条件和必要的验证。", system_prompt="审查用户提供的代码。", enabled=True),
            dict(id="qa-writing", name="产品文案助手", description="把产品信息写成简洁清楚的中文，保持统一的表达方式。", system_prompt="编辑用户提供的文案。", enabled=False),
        ])
        window.config_manager.set_mcp_servers([
            dict(id="qa-docs", name="项目文档", description="搜索和阅读项目文档，查找接口与使用说明。", type="streamable_http", url="https://example.invalid/mcp", enabled=False),
            dict(id="qa-data", name="数据查询", description="读取团队授权的数据，并查询业务报表。", type="streamable_http", url="https://example.invalid/data", enabled=True),
            dict(id="qa-managed", name="技能配套服务", type="stdio", command="example-tool", managed_by_skill="sample-skill", enabled=True),
        ])

        def settle():
            QTest.qWait(150)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()

        def capture(name, width=1120, height=820):
            window.resize(width, height)
            settle()
            if page.current_section == "skills":
                assert page._column_count == store_columns(page.scroll.viewport().width())
            for card in window.findChildren(CapabilityCard):
                if not card.isVisible():
                    continue
                for child in card.findChildren(QPushButton) + card.findChildren(ClampedText):
                    if child.isVisible():
                        rect = QRect(child.mapTo(card, QPoint()), child.size())
                        assert card.rect().contains(rect), (name, child.objectName(), rect, card.rect())
            assert window.grab().save(str(args.output / f"{name}-{scale}.png"), "PNG")

        try:
            window.resize(1120, 820)
            window.show()
            assert window.open_skills_center()
            page = window.product_pages[window.PAGE_CAPABILITIES]
            capture("skills")
            capture("skills-small", 900, 680)
            assert page.select_section("mcp")
            capture("mcp")
            mcp = page.management_page.mcp_server_manager
            mcp._testing_server = mcp.get_servers()[0]
            mcp._handle_test_result({"ok": False, "error": "演示状态：无法连接到服务，请检查地址后重试。"})
            capture("mcp-error-small", 900, 680)
            mcp.search_input.setText("不存在的服务")
            capture("mcp-empty", 900, 680)
            mcp.search_input.clear()
            mcp._server_action("qa-docs", mcp.edit_server)
            capture("mcp-editor", 900, 680)
            assert mcp.confirm_editor()
            assert page.select_section("agents")
            capture("experts")
            experts = page.management_page.agent_profile_manager
            experts._profile_action("qa-review", experts.open_profile_editor)
            capture("expert-editor-small", 900, 680)
            assert experts.leave_profile_editor()
            capture("experts-small", 900, 680)
            saved_color = DesignTokens.primary
            theme.repository.write_preview(name="能力页主题预览", overrides={"tokens": {
                "primary": "#39734d", "primary_hover": "#2d603e", "bg_main": "#f8fbf8",
            }}, default_tokens=default_design_tokens())
            assert theme.apply_repository_state(reason="capability_cards_preview"), theme.last_error
            capture("experts-preview", 900, 680)
            assert theme.restore_saved_theme()
            assert DesignTokens.primary == saved_color
            capture("experts-restored", 900, 680)
            if scale == "1":
                dark = {
                    "primary": "#8b93ff", "primary_hover": "#9ca3ff", "primary_soft": "#252a43",
                    "primary_focus": "#6972cc", "bg_app": "#101116", "bg_main": "#15171d",
                    "bg_secondary": "#1b1e26", "bg_hover": "#242832", "bg_pressed": "#2a2f3a",
                    "bg_sidebar": "#12141a", "bg_sidebar_hover": "#1c2029", "bg_sidebar_selected": "#23283a",
                    "management_bg": "#101116", "management_panel_bg": "#181b22", "text_primary": "#eceef3",
                    "text_secondary": "#b7bcc8", "text_tertiary": "#858c9b", "text_disabled": "#666d7a",
                    "sidebar_text": "#eceef3", "sidebar_text_muted": "#aeb4c2", "sidebar_border": "#292d36",
                    "icon_primary": "#eceef3", "icon_secondary": "#aeb4c2", "border": "#343946",
                    "border_strong": "#474d5b", "border_subtle": "#292d36", "separator": "#292d36",
                    "success_text": "#6ecf9b", "warning_text": "#f0b86e",
                }
                theme.repository.write_preview(name="深色能力页预览", overrides={"tokens": dark}, default_tokens=default_design_tokens())
                assert theme.apply_repository_state(reason="capability_cards_dark"), theme.last_error
                capture("experts-dark")
                assert page.select_section("mcp")
                capture("mcp-dark")
                assert page.select_section("skills")
                capture("skills-dark")
                assert theme.restore_saved_theme()
                assert DesignTokens.primary == saved_color
            print("capability pages, small windows, editor return, theme preview/cancel: PASS", flush=True)
        finally:
            stopped = []
            window._shutdown_workers_async(lambda _preserved: stopped.append(True))
            deadline = time.monotonic() + 30
            while not stopped and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(20)
            assert stopped, "UI workers did not finish closing"
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    run()

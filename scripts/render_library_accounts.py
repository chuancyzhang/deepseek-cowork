"""Capture source sign-in, recovery, narrow windows and safe theme preview.

Run with QT_SCALE_FACTOR=1 or 1.5. All accounts and responses are local fixtures.
"""
from pathlib import Path
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "test")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")


def main():
    with tempfile.TemporaryDirectory(prefix="cowork-library-accounts-", ignore_cleanup_errors=True) as temporary:
        os.environ["APPDATA"] = temporary
        os.environ["LOCALAPPDATA"] = temporary
        config_dir = Path(temporary) / "DeepSeekCowork"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QPushButton
        from core.knowledge_library import KnowledgeError, KnowledgeService, KnowledgeStore
        from core.knowledge_sources import MultiSourceKnowledgeService, TencentDocsProvider, LexiangProvider
        from core.config_manager import ConfigManager
        from core.enterprise_client import EnterpriseStore
        from core.theme import ThemeRuntimeManager, DesignTokens, default_design_tokens
        from core.theme_service import ThemeRepository
        from ui.knowledge_sources import MultiSourceKnowledgePage
        from test_knowledge_library import TestProtector, WeKnoraFixture, CloudMcpFixture
        from main import SettingsDialog, initialize_desktop_theme

        app = QApplication.instance() or QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf", "consolab.ttf"):
            path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        initialize_desktop_theme(app)
        theme = ThemeRuntimeManager(app, ThemeRepository(str(Path(temporary) / "themes")))
        app.theme_manager = theme
        assert theme.apply_repository_state(reason="library_accounts_capture"), theme.last_error
        store = KnowledgeStore(str(Path(temporary) / "library"), TestProtector())
        remote = CloudMcpFixture()
        service = MultiSourceKnowledgeService(store, {
            "weknora": KnowledgeService(store, WeKnoraFixture()),
            "tencent-docs": TencentDocsProvider(store, remote, remote.call, remote.discover),
            "lexiang": LexiangProvider(store, remote, remote.call, remote.discover),
        })
        output = ROOT / "docs/images/library-accounts"
        output.mkdir(parents=True, exist_ok=True)
        scale = os.environ.get("QT_SCALE_FACTOR", "1").replace(".", "_")
        page = MultiSourceKnowledgePage(service=service)

        def wait(predicate):
            deadline = time.monotonic() + 5
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(10)
            assert predicate(), "fixture UI did not settle"

        def capture(widget, name):
            app.processEvents()
            QTest.qWait(80)
            assert not any("资料服务连接" in b.text() for b in widget.findChildren(QPushButton))
            path = output / f"{name}-{scale}.png"
            assert widget.grab().save(str(path))
            print(path, flush=True)

        try:
            for source in service.providers:
                page.sources.setCurrentIndex(page.sources.findData(source))
                inner = page.page(source)
                page.resize(1000, 740)
                page.show()
                capture(page, source + "-login")
                assert page.sources.mapTo(inner.sidebar, QPoint()).y() < 40
                page.resize(660, 640)
                capture(page, source + "-login-narrow")
                assert page.sources.mapTo(inner.sidebar, QPoint()).y() < 40
                assert page.source_views[source].horizontalScrollBar().maximum() == 0
                page.resize(1000, 740)
                provider = service.provider(source)
                if source == "weknora":
                    provider.login("http://localhost", "reader@example.test", "password")
                elif source == "tencent-docs":
                    provider.start_authorization()
                    provider.finish_authorization(confirmed=True)
                else:
                    provider.connect("acme", "sample-token")
                inner.refresh()
                wait(lambda: inner.tree.topLevelItemCount() >= 4)
                inner.open_kb({"id": "kb-a" if source == "weknora" else "space", "name": "产品资料"})
                wait(lambda: inner.items.count() > 0)
                # Representative fixture rows exercise types, metadata, long names and indexing states.
                for index, title in enumerate(("用户访谈纪要.docx", "季度数据.xlsx", "产品研究报告.pdf", "面向多个项目的资料整理与团队协作说明.md")):
                    document = {"id": f"sample-{index}", "knowledge_base_id": inner.current_kb["id"], "title": title,
                                "updated_at": "2026-10-22T09:30:00+08:00", "parse_status": "processing" if index == 3 else "completed"}
                    inner.add_item(title, {"document": document, "ref": provider.reference(inner.scope, inner.current_kb["id"], title, document["id"])})
                capture(page, source + "-connected")
                page.resize(660, 740)
                capture(page, source + "-narrow")
                assert page.size().width() == 660 and page.size().height() == 740
                assert page.source_views[source].horizontalScrollBar().maximum() == 0
                if source == "weknora":
                    page.resize(1000, 740)
                    for item in inner.items._rows:
                        document = item.data(Qt.UserRole).get("document", {})
                        store.visit(provider.reference(inner.scope, inner.current_kb["id"], document["title"], document["id"]))
                    inner.nav_buttons[0].click()
                    capture(page, "weknora-recent")
                    provider.transport.wiki_pages = [
                        {"slug": "index", "title": "产品资料概览", "page_type": "index"},
                        {"slug": "concept", "title": "用户需求与验证方法", "page_type": "concept", "category_path": ["产品研究"]},
                        {"slug": "summary", "title": "访谈纪要摘要", "page_type": "summary"}]
                    inner.show_wiki()
                    wait(lambda: inner.items.count() == 3)
                    capture(page, "weknora-wiki")
                    inner.files_button.click()
                    wait(lambda: inner.items.count() == 1)
                    page.resize(660, 740)
                inner.items.item(0).setCheckState(0, Qt.Checked)
                inner.reconnect()
                if source == "weknora":
                    inner.password.setText("incorrect-password")
                    with patch.object(provider, "login", side_effect=KnowledgeError("unauthenticated", "账号或密码不正确，请检查后重新登录。")):
                        inner.login()
                        wait(lambda: inner.login_button.isEnabled())
                elif source == "lexiang":
                    inner.company.setText("acme")
                    inner.token.setText("expired-token")
                    with patch.object(provider, "connect", side_effect=KnowledgeError("unauthenticated", "Token 已失效，请在官方配置页续期后重新连接。")):
                        inner.connect_source()
                        wait(lambda: inner.connect_button.isEnabled())
                else:
                    inner.error(KnowledgeError("unauthenticated", "授权已失效，请重新打开授权页面。"))
                assert inner.items.item(0).checkState(0) == Qt.Checked
                capture(page, source + "-relogin-error")

            saved = DesignTokens.primary
            theme.repository.write_preview(name="资料库主题预览", overrides={"tokens": {"primary": "#39734d", "primary_hover": "#2d603e"}}, default_tokens=default_design_tokens())
            assert theme.apply_repository_state(reason="library_accounts_preview"), theme.last_error
            capture(page, "theme-preview")
            assert theme.restore_saved_theme()
            assert DesignTokens.primary == saved

            config = ConfigManager()
            EnterpriseStore(config.data_dir).write({"connection": {"schema_version": 1, "config_version": 1,
                "platform_id": "sample-company", "platform_name": "示例企业", "platform_url": "https://cowork.example.invalid",
                "distribution_channel": "enterprise"}, "client_id": "sample", "generation": "sample"})
            settings = SettingsDialog(config, initial_page_label="企业账号")
            try:
                assert "企业账号" in settings._page_labels
                assert not {"账号与连接", "资料服务连接"} & set(settings._page_labels)
                settings.resize(1040, 760)
                settings.show()
                capture(settings, "settings-enterprise")
                settings.settings_search.setText("资料服务连接")
                assert all(settings.nav_list.item(i).isHidden() for i in range(settings.nav_list.count()))
            finally:
                settings.account_connections_page.enterprise.jobs.pool.shutdown(wait=True)
                settings._allow_close_without_prompt = True
                settings.close()
                settings.deleteLater()
        finally:
            for inner in page.pages.values():
                inner.upload_timer.stop()
                inner.jobs.pool.shutdown(wait=True)
            app.processEvents()
            page.close()
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        print("Source sign-in/recovery, 660px width, settings removal and theme preview: PASS", flush=True)


if __name__ == "__main__":
    main()

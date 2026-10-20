"""Exercise native collaboration against the real center API in isolated storage."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QTreeWidget, QWidget

from core.chat_storage import ChatStorage
from core.enterprise_client import EnterpriseStore
from core.team_space_storage import TeamSpaceStore
from core.team_space_storage import TeamSpacePaused
from core.team_spaces import TeamSpaceClient
from ui.team_spaces import TeamSpacesPage
import test_team_spaces as backend_tests
from test_team_space_client import Protector


class TeamSpaceNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        # Offscreen Windows has no platform font fallback; load the app's font.
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
        cls.qt.setFont(QFont("Microsoft YaHei", 10))

    def setUp(self):
        self.backend = backend_tests.TeamSpaceTests()
        self.backend.setUp()
        self.addCleanup(self.backend.doCleanups)
        self.backend.join()
        self.backend.command("owner", "space.update", revision=1, allow_personal=True)
        root = Path(self.backend.temp.name) / "desktop"
        self.enterprise = EnterpriseStore(root, protector=Protector())
        self.enterprise.write({"connection": {"platform_id": "test", "platform_url": "http://localhost", "platform_name": "方案协作企业"},
            "member": {"id": "owner", "name": "空间所有者"}, "generation": "one", "expires_at": time.time() + 3600,
            "client_id": "native-test", "models": []}, self.backend.tokens["owner"])
        self.local = TeamSpaceStore(root)
        def send(request):
            response = self.backend.client.request(request.method, str(request.url), headers=dict(request.headers), content=request.read())
            return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)
        def factory():
            return TeamSpaceClient(self.enterprise, self.local, transport=httpx.MockTransport(send))
        self.host = QWidget()
        self.host.chat_storage = ChatStorage(str(root / "chat_history.sqlite"))
        self.host.config_manager = SimpleNamespace(iter_model_profiles=lambda: [{"id": "personal", "display_name": "个人模型", "model_name": "test"}], get_projects=lambda **_: [])
        self.host._available_session_skills = lambda: []
        self.host.open_settings = lambda: None
        self.host.team_space_share_source = lambda _: {"messages": [{"role": "assistant", "content": "选定回答"}], "state": "stopped", "title": "未完成的任务", "source_ref": "task"}
        self.page = TeamSpacesPage(self.host, client_factory=factory)
        self.page.resize(1000, 760)
        self.confirm = patch("ui.team_spaces.QMessageBox.question", return_value=16384)
        self.confirm.start()
        self.addCleanup(self.confirm.stop)
        self.page.show()
        self.wait()
        self.addCleanup(self.close)

    def close(self):
        self.page.timer.stop()
        self.page.pool.shutdown(wait=True)
        self.page.close()
        self.page.deleteLater()
        self.host.deleteLater()
        self.qt.processEvents()

    def wait(self):
        until = time.time() + 15
        for _ in range(3):
            self.qt.processEvents()
            QTest.qWait(10)
        while self.page.busy and time.time() < until:
            self.qt.processEvents()
            QTest.qWait(10)
        self.assertFalse(self.page.busy, self.page.status.text())
        self.qt.processEvents()

    def click(self, text):
        self.qt.processEvents()
        buttons = [b for b in self.page.findChildren(QPushButton) if b.text() == text and b.isVisible()]
        self.assertTrue(buttons, text)
        buttons[-1].click()
        self.wait()

    def open_space(self, tab="activity"):
        self.page.open_space(self.backend.space, tab)
        self.wait()

    def shot(self, name):
        directory = os.environ.get("COWORK_SPACE_SCREENSHOTS")
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.page.grab().save(str(Path(directory) / (name + ".png")))

    def test_list_loading_stays_compact_and_failure_can_retry(self):
        original = self.page.client.list
        release = threading.Event()
        def delayed(**filters):
            if not release.wait(10):
                raise RuntimeError("Test did not release the list request")
            return original(**filters)
        with patch.object(self.page.client, "list", side_effect=delayed):
            try:
                self.page.list()
                for name, width, height in (("wide", 1440, 960), ("narrow", 480, 640)):
                    self.page.resize(width, height)
                    self.qt.processEvents()
                    headings = [label for label in self.page.scroll.widget().findChildren(QLabel)
                                if label.property("spaceTextRole") == "heading"]
                    self.assertEqual([label.text() for label in headings], ["我的空间"])
                    heading = headings[0]
                    subtitle = self.page.layout.itemAt(1).widget()
                    self.assertLess(heading.height(), 60)
                    self.assertLess(subtitle.height(), 90)
                    self.assertLessEqual(subtitle.y() - heading.geometry().bottom(), 16)
                    self.assertTrue(self.page.loading_indicator.isVisible())
                    self.assertFalse(self.page.refresh_button.isEnabled())
                    self.assertFalse(self.page.back_button.isVisible())
                    self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(), 0)
                    self.shot("desktop-list-loading-" + name)
            finally:
                release.set()
            self.wait()
        self.assertFalse(self.page.loading_indicator.isVisible())
        self.assertTrue(self.page.findChildren(QTreeWidget))
        self.shot("desktop-list-narrow")

        with patch.object(self.page.client, "list", side_effect=TeamSpacePaused("企业中心暂时无法连接，请检查网络。")):
            self.page.list()
            self.wait()
        self.assertFalse(self.page.loading_indicator.isVisible())
        self.assertTrue(self.page.refresh_button.isEnabled())
        self.assertEqual(self.page.refresh_button.text(), "重试")
        self.assertIn("无法连接", self.page.status.text())
        self.assertFalse(any(label.text() == "还没有加入空间"
                             for label in self.page.scroll.widget().findChildren(QLabel)))
        self.shot("desktop-list-error")
        self.click("重试")
        self.assertEqual(self.page.refresh_button.text(), "刷新")
        self.assertEqual(self.page.findChildren(QTreeWidget)[0].topLevelItemCount(), 1)
        self.page.resize(1440, 960)
        self.qt.processEvents()
        self.shot("desktop-list")

    def test_empty_list_invitation_and_search_have_distinct_states(self):
        state = self.enterprise.read()
        state.update(member={"id": "outsider", "name": "新同事"}, generation="new-member")
        self.enterprise.write(state, self.backend.tokens["outsider"])
        self.page.refresh()
        self.wait()
        self.assertTrue(any(label.text() == "还没有加入空间"
                            for label in self.page.scroll.widget().findChildren(QLabel)))
        self.assertEqual([button.text() for button in self.page.findChildren(QPushButton)
                          if button.isVisible() and button.property("spaceKind") == "primary"], ["新建空间"])
        self.page.resize(480, 640)
        self.qt.processEvents()
        self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(), 0)
        self.shot("desktop-list-empty")
        self.click("新建空间")
        self.assertEqual(self.page.fields["name"].text(), "")
        self.click("返回列表")

        self.backend.command("owner", "invite.create", member_id="outsider", role="member")
        self.page.refresh()
        self.wait()
        invitations = self.page.findChildren(QTreeWidget)[0]
        invitations.setCurrentItem(invitations.topLevelItem(0))
        self.click("接受邀请")
        self.assertEqual(self.page.findChildren(QTreeWidget)[0].topLevelItemCount(), 1)

        query = next(field for field in self.page.findChildren(QLineEdit)
                     if field.accessibleName() == "搜索已加入空间")
        query.setText("不存在的空间")
        self.click("搜索")
        self.assertTrue(any(label.text() == "没有找到匹配的空间"
                            for label in self.page.scroll.widget().findChildren(QLabel)))
        self.shot("desktop-list-search-empty")
        query = next(field for field in self.page.findChildren(QLineEdit)
                     if field.accessibleName() == "搜索已加入空间")
        query.clear()
        QTest.keyClick(query, Qt.Key_Return)
        self.wait()
        self.assertEqual(self.page.findChildren(QTreeWidget)[0].topLevelItemCount(), 1)
        self.backend.command("owner", "space.update", revision=self.backend.get()["revision"],
                             name="跨部门客户方案与交付资料协作空间 · " * 4)
        self.open_space()
        heading = self.page.layout.itemAt(0).widget()
        self.assertGreater(heading.height(), heading.fontMetrics().height() * 2)
        self.assertGreaterEqual(heading.height(), heading.heightForWidth(heading.width()))

    def test_complete_native_plan_upload_task_and_share(self):
        self.open_space("plans")
        self.click("新建计划")
        self.page.fields["title"].setText("客户方案初稿")
        self.page.fields["description"].setPlainText("按共享需求整理方案，明确验收边界。")
        self.page.fields["assignee_id"].setCurrentIndex(2)
        self.click("保存计划")
        plans = self.backend.get(suffix="/plans")["items"]
        self.assertEqual(plans[0]["title"], "客户方案初稿")
        self.open_space("assets")
        self.click("上传资料")
        path = Path(self.backend.temp.name) / "需求.txt"
        path.write_text("客户需求", encoding="utf-8")
        self.page.fields["path"].setText(str(path))
        self.page.fields["description"].setPlainText("原始需求")
        self.click("上传并发布")
        self.assertEqual(len(self.backend.get(suffix="/assets")["items"]), 1, self.page.status.text())
        self.open_space("tasks")
        self.click("开始任务")
        self.assertEqual(self.page.fields["model"].currentText(), "我配置的 · 个人模型")
        self.page.fields["title"].setText("本机方案任务")
        self.page.fields["prompt"].setPlainText("根据需求起草方案")
        self.page.fields["directory"].setText(str(Path(self.backend.temp.name) / "space-work"))
        captured = []
        self.page.taskRequested.connect(captured.append)
        self.click("创建任务并打开对话")
        self.assertEqual(len(captured), 1, self.page.status.text())
        self.assertEqual(captured[0]["binding"]["space_id"], self.backend.space)
        self.page.share_form(task_id="task")
        self.wait()
        self.page.fields["body"].setPlainText("已完成第一部分，后续需要补充。")
        self.click("发布成果")
        shares = self.backend.get(suffix="/shares")["items"]
        self.assertEqual(len(shares), 1, self.page.status.text())
        snapshot = self.backend.get(suffix="/objects/share/" + shares[0]["id"])["snapshots"][0]
        self.assertEqual(snapshot["source_state"], "stopped")
        self.assertEqual(snapshot["body"], "已完成第一部分，后续需要补充。")
        self.open_space("plans")
        self.shot("desktop-plans")
        self.page.resize(640, 650)
        self.qt.processEvents()
        self.shot("desktop-plans-narrow")

    def test_settings_and_draft_survive_navigation(self):
        self.open_space()
        self.page.settings(self.page.space)
        self.page.fields["description"].setPlainText("保留的设置草稿")
        self.open_space()
        self.page.settings(self.page.space)
        self.assertEqual(self.page.fields["description"].toPlainText(), "保留的设置草稿")
        self.click("保存设置")
        self.assertEqual(self.backend.get()["description"], "保留的设置草稿")
        self.page.members()
        self.wait()
        self.shot("desktop-members")

    def test_account_switch_drops_late_results_and_drafts_remain_scoped(self):
        self.open_space()
        self.page.comment_form("space", self.backend.space)
        self.page.fields["body"].setPlainText("原账号草稿")
        state = self.enterprise.read()
        state.update(member={"id": "member", "name": "新账号"}, generation="two")
        self.enterprise.write(state, self.backend.tokens["member"])
        self.page.refresh()
        self.wait()
        self.assertEqual(self.page.client.identity["member_id"], "member")
        self.open_space()
        self.page.comment_form("space", self.backend.space)
        self.assertEqual(self.page.fields["body"].toPlainText(), "")
        self.assertEqual(self.local.draft({"platform_id": "test", "member_id": "owner"}, self.backend.space, "comment:space" + self.backend.space)["values"]["body"], "原账号草稿")

    def test_sign_in_guidance_preserves_draft_and_returns_after_login(self):
        self.open_space()
        self.page.comment_form("space", self.backend.space)
        self.page.fields["body"].setPlainText("登录过期前的草稿")
        state = self.enterprise.read()
        self.enterprise.write({**state, "expires_at": 0}, self.backend.tokens["owner"])
        self.page.refresh()
        self.wait()
        self.assertFalse(self.page.toolbar.isVisible())
        self.assertFalse(self.page.status.isVisible())
        self.host.open_settings = Mock()
        self.page.sign_in_state.action_button.click()
        self.host.open_settings.assert_called_once_with("账号与连接")
        self.shot("desktop-sign-in-expired")
        self.page.resize(480, 560)
        self.wait()
        self.shot("desktop-sign-in-narrow")

        self.enterprise.write({**state, "generation": "signed-in-again"}, self.backend.tokens["owner"])
        self.click("我已登录，刷新")
        self.assertTrue(self.page.toolbar.isVisible())
        self.open_space()
        self.page.comment_form("space", self.backend.space)
        self.assertEqual(self.page.fields["body"].toPlainText(), "登录过期前的草稿")

        self.enterprise.write({"connection": state["connection"]})
        self.page.refresh()
        self.wait()
        self.shot("desktop-sign-in")
        self.enterprise.write({})
        self.page.refresh()
        self.wait()
        self.shot("desktop-connect-enterprise")

    def test_lost_publish_ack_recovers_once_without_reuploading_consumed_files(self):
        self.open_space("tasks")
        self.page.share_form()
        self.wait()
        path = Path(self.backend.temp.name) / "附件.txt"
        path.write_text("固定版本", encoding="utf-8")
        self.page.fields["title"].setText("仅发布一次")
        self.page.fields["body"].setPlainText("选定内容")
        self.page.fields["files"].setPlainText(str(path))
        original = self.page.client.execute
        def lost_ack(space, action, payload, **kwargs):
            result = original(space, action, payload, **kwargs)
            if action == "share.publish":
                raise TeamSpacePaused("确认响应丢失")
            return result
        with patch.object(self.page.client, "execute", side_effect=lost_ack):
            self.click("发布成果")
        self.assertIn("确认响应丢失", self.page.status.text())
        self.assertEqual(self.page.refresh_button.text(), "刷新")
        self.click("发布成果")
        shares = self.backend.get(suffix="/shares")["items"]
        self.assertEqual(len(shares), 1, self.page.status.text())
        self.assertEqual(len(self.backend.get(suffix="/assets")["items"]), 1)

    def test_main_window_binds_before_first_send_without_global_selection_change(self):
        from main import MainWindow
        self.open_space("tasks")
        binding = self.local.bind_workspace(self.page.client.identity, self.backend.space, Path(self.backend.temp.name) / "task-directory")
        state = SimpleNamespace(session_id="new-task", persisted_conversation_meta={})
        from PySide6.QtWidgets import QPlainTextEdit
        composer = QPlainTextEdit()
        sent, errors = [], []
        def send():
            saved = self.host.chat_storage.get_team_space_binding(state.session_id)
            self.assertEqual(saved, binding)
            sent.append(composer.toPlainText())
        host = SimpleNamespace(PAGE_TEAM_SPACES="spaces", product_pages={"spaces": self.page},
            chat_storage=self.host.chat_storage, create_new_session=lambda **_: state.session_id,
            get_session=lambda _: state, _set_session_workspace=lambda s, p, **_: setattr(s, "workspace_dir", p),
            _compose_session_meta=lambda s: {"workspace_dir": s.workspace_dir, "workspace_source": "team_space"},
            activate_session=lambda _: None, show_conversation_page=lambda: None, input_field=composer,
            refresh_model_selector=lambda: None, refresh_selected_skill_controls=lambda _: None,
            handle_send=send, add_system_toast=lambda text, *a, **k: errors.append(text))
        MainWindow.start_team_space_task(host, {"binding": binding, "title": "空间任务", "prompt": "原始输入",
            "model_id": "personal", "skills": []})
        self.assertEqual(errors, [])
        self.assertEqual(sent, ["原始输入"])
        self.assertEqual(self.host.chat_storage.list_conversations_by_workspace(), {})
        composer.deleteLater()

    def test_theme_preview_cancel_preserves_saved_theme_and_draft(self):
        from core.theme import ThemeRuntimeManager, DesignTokens, bind_theme, default_design_tokens
        from core.theme_service import ThemeRepository
        for filename in ("consola.ttf", "segoeui.ttf", "segoeuib.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
        theme = ThemeRuntimeManager(self.qt, ThemeRepository(str(Path(self.backend.temp.name) / "themes")))
        self.assertTrue(theme.apply_repository_state(reason="team_space_qa"), theme.last_error)
        bind_theme(self.page, self.page.apply_theme)
        self.open_space("plans")
        saved = DesignTokens.primary
        theme.repository.write_preview(name="团队空间预览", overrides={"tokens": {"primary": "#39734d", "primary_hover": "#2d603e"}}, default_tokens=default_design_tokens())
        self.assertTrue(theme.apply_repository_state(reason="team_space_preview"), theme.last_error)
        self.shot("desktop-theme-preview")
        state = self.enterprise.read()
        self.enterprise.write({**state, "expires_at": 0}, self.backend.tokens["owner"])
        self.page.refresh()
        self.wait()
        self.shot("desktop-sign-in-theme-preview")
        self.assertTrue(theme.restore_saved_theme(), theme.last_error)
        self.assertEqual(DesignTokens.primary, saved)
        theme.stop()

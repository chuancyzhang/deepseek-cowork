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
from PySide6.QtGui import QAction, QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QTreeWidget, QWidget

from core.chat_storage import ChatStorage
from core.enterprise_client import EnterpriseStore
from core.team_space_storage import TeamSpaceStore
from core.team_space_storage import TeamSpaceError, TeamSpacePaused
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
                QTest.qWait(230)
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
        self.assertFalse(any(label.isVisible() and label.text() == "还没有加入空间"
                             for label in self.page.scroll.widget().findChildren(QLabel)))
        self.shot("desktop-list-error")
        self.click("重试")
        self.assertFalse(self.page.refresh_button.isVisible())
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
        self.click("取消")

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
        QTest.keyClick(query, Qt.Key_Return)
        self.wait()
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
        heading = self.page.space_title
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
        self.click("我的任务")
        self.click("新建任务")
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

    def test_cached_tabs_and_refresh_preserve_shell_rows_selection_and_scroll(self):
        for index in range(16):
            self.backend.command("owner", "plan.create", title=f"客户方案阶段 {index:02}", assignee_id="owner")
        self.open_space("plans")
        shell = self.page.space_header
        tree = self.page.main_table
        tree.setCurrentItem(tree.topLevelItem(6))
        selected = tree.currentItem().data(0, Qt.UserRole)["id"]
        self.page.scroll.verticalScrollBar().setValue(170)
        scroll = self.page.scroll.verticalScrollBar().value()
        original = self.page.client.collection
        release = threading.Event()
        def delayed(space_id, collection, **filters):
            if collection == "plans" and not release.wait(10):
                raise RuntimeError("Read was not released")
            return original(space_id, collection, **filters)
        with patch.object(self.page.client, "collection", side_effect=delayed), patch.object(self.page.client, "detail", wraps=self.page.client.detail) as detail:
            try:
                self.page.refresh()
                self.qt.processEvents()
                self.assertIs(self.page.main_table, tree)
                self.assertIs(self.page.space_header, shell)
                self.assertEqual(tree.topLevelItemCount(), 16)
                self.assertEqual(tree.currentItem().data(0, Qt.UserRole)["id"], selected)
                self.assertEqual(self.page.scroll.verticalScrollBar().value(), scroll)
                self.shot("desktop-plans-refresh")
            finally:
                release.set()
            self.wait()
            self.assertEqual(detail.call_count, 0, "Unchanged collections must not refetch members/models")
        self.assertIs(self.page.main_table, tree)
        self.backend.command("owner", "plan.create", title="新增共享计划")
        self.page._poll()
        self.wait()
        self.assertIs(self.page.main_table, tree)
        self.assertEqual(tree.topLevelItemCount(), 17)
        self.assertEqual(tree.currentItem().data(0, Qt.UserRole)["id"], selected)
        self.open_space("assets")
        release.clear()
        with patch.object(self.page.client, "collection", side_effect=delayed):
            try:
                self.page.tab_buttons["plans"].click()
                self.qt.processEvents()
                self.assertEqual(self.page.main_table.topLevelItemCount(), 17, "Visited tabs render their cache before the network returns")
                self.assertIs(self.page.space_header, shell)
                self.assertEqual(self.page.scroll.verticalScrollBar().value(), scroll)
            finally:
                release.set()
            self.wait()

    def test_first_space_load_keeps_navigation_and_disables_unready_writes(self):
        original = self.page.client.detail
        release = threading.Event()
        def delayed(space_id):
            if not release.wait(10):
                raise RuntimeError("Initial load was not released")
            return original(space_id)
        with patch.object(self.page.client, "detail", side_effect=delayed):
            try:
                self.page.open_space(self.backend.space)
                QTest.qWait(230)
                self.assertTrue(self.page.space_header.isVisible())
                self.assertEqual(self.page.space_title.text(), self.backend.get()["name"])
                self.assertEqual(self.page.route[1]["tab"], "plans")
                self.assertTrue(all(button.isVisible() for button in self.page.tab_buttons.values()))
                self.assertTrue(self.page._skeleton.isVisible())
                self.assertFalse(next(b for b in self.page.findChildren(QPushButton) if b.text() == "新建计划").isEnabled())
                self.assertTrue(self.page._skeleton.styleSheet())
                search = next(w for w in self.page.scroll.widget().findChildren(QLineEdit) if w.accessibleName() == "搜索当前内容")
                self.assertGreaterEqual(search.height(), 32)
                self.shot("desktop-first-load")
            finally:
                release.set()
            self.wait()

    def test_late_read_cannot_replace_new_tab_or_clear_its_busy_state(self):
        self.backend.command("owner", "plan.create", title="迟到的计划")
        self.open_space("plans")
        original = self.page.client.collection
        started, release = threading.Event(), threading.Event()
        def delayed(space_id, collection, **filters):
            if collection == "plans":
                started.set()
                if not release.wait(10):
                    raise RuntimeError("Read was not released")
            return original(space_id, collection, **filters)
        with patch.object(self.page.client, "collection", side_effect=delayed):
            try:
                self.page.refresh()
                until = time.time() + 2
                while not started.is_set() and time.time() < until:
                    QTest.qWait(10)
                self.assertTrue(started.is_set())
                self.page.tab_buttons["tasks"].click()
                self.wait()
                self.assertEqual(self.page.route[1]["tab"], "tasks")
                self.assertEqual(self.page.main_table.headerItem().text(0), "成果")
            finally:
                release.set()
            QTest.qWait(200)
            self.assertFalse(self.page.busy)
            self.assertEqual(self.page.route[1]["tab"], "tasks")
            self.assertEqual(self.page.main_table.topLevelItemCount(), 0)

    def test_offline_preserves_read_content_and_form_input_then_recovers(self):
        self.backend.command("owner", "plan.create", title="已加载的计划")
        self.open_space("plans")
        tree = self.page.main_table
        with patch.object(self.page.client, "collection", side_effect=TeamSpacePaused("连接已中断")):
            self.page.refresh()
            self.wait()
        self.assertIs(self.page.main_table, tree)
        self.assertEqual(tree.topLevelItemCount(), 1)
        self.assertIn("上次加载", self.page.status.text())
        create = next(b for b in self.page.findChildren(QPushButton) if b.text() == "新建计划" and b.isVisible())
        self.assertFalse(create.isEnabled())
        self.shot("desktop-offline")
        self.click("重试")
        self.assertTrue(create.isEnabled())
        self.click("新建计划")
        field = self.page.fields["title"]
        field.setText("网络恢复后继续编辑")
        with patch.object(self.page.client, "collection", side_effect=TeamSpacePaused("连接已中断")):
            self.page._poll()
            self.wait()
        self.assertIs(self.page.fields["title"], field)
        self.assertEqual(field.text(), "网络恢复后继续编辑")
        self.page._poll()
        self.wait()
        self.assertEqual(field.text(), "网络恢复后继续编辑")
        self.click("保存计划")
        self.assertTrue(any(p["title"] == "网络恢复后继续编辑" for p in self.backend.get(suffix="/plans")["items"]))

    def test_revoked_access_clears_cached_shared_content_but_keeps_my_tasks_entry(self):
        self.backend.command("owner", "plan.create", title="受保护的计划")
        state = self.enterprise.read()
        self.enterprise.write({**state, "member": {"id": "member", "name": "同事"}, "generation": "member-session"}, self.backend.tokens["member"])
        self.page.refresh()
        self.wait()
        self.open_space("plans")
        self.page.settings(self.page.space)
        self.wait()
        self.page.refresh()
        self.wait()
        self.assertEqual(self.page.route[0], "settings_view")
        self.page.go_back()
        self.wait()
        member = next(m for m in self.backend.get()["members"] if m["id"] == "member")
        self.backend.command("owner", "member.remove", member_id="member", revision=member["revision"])
        self.page.refresh()
        self.wait()
        self.assertFalse(any(key[0] == self.backend.space for key in self.page._cache))
        self.assertFalse(self.page.scroll.widget().findChildren(QTreeWidget))
        self.assertTrue(self.page.my_tasks_button.isEnabled())
        self.shot("desktop-permission-changed")
        self.click("我的任务")
        self.assertEqual(self.page.route[0], "my_tasks")
        self.assertTrue(any(label.text() == "我的任务" for label in self.page.scroll.widget().findChildren(QLabel)))

    def test_archived_space_recovers_connection_and_restores_from_management_menu(self):
        self.backend.command("owner", "space.archive", revision=self.backend.get()["revision"])
        self.open_space("plans")
        self.page.settings(self.page.space)
        actions = {a.text(): a for a in self.page.findChildren(QAction)}
        restore, transfer = actions["恢复空间"], actions["转移所有权"]
        self.assertTrue(restore.isEnabled())
        self.assertTrue(transfer.isEnabled())
        save = next(b for b in self.page.findChildren(QPushButton) if b.text() == "保存设置")
        self.assertFalse(save.isEnabled())
        with patch.object(self.page.client, "collection", side_effect=TeamSpacePaused("连接已中断")):
            self.page._poll()
            self.wait()
        self.assertFalse(restore.isEnabled())
        self.assertFalse(transfer.isEnabled())
        self.page._poll()
        self.wait()
        self.assertTrue(restore.isEnabled())
        restore.trigger()
        self.wait()
        self.assertEqual(self.backend.get()["state"], "active")
        self.assertTrue(next(b for b in self.page.findChildren(QPushButton) if b.text() == "新建计划" and b.isVisible()).isEnabled())

    def test_folder_navigation_fixed_version_and_return_restore_filter(self):
        folder = self.backend.command("owner", "folder.create", name="需求调研")
        asset = self.backend.asset(name="客户需求.txt", folder_id=folder["id"])
        self.backend.asset(b"version two", asset_id=asset["id"], revision=1)
        self.backend.asset(b"scope", name="交付边界.txt")
        self.backend.asset(b"style", name="写作规范.txt")
        self.open_space("assets")
        tree = self.page.main_table
        last = tree.topLevelItem(tree.topLevelItemCount() - 1)
        self.assertLessEqual(tree.visualItemRect(last).bottom(), tree.viewport().height())
        self.shot("desktop-assets-root")
        row = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).data(0, Qt.UserRole).get("_folder"))
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(row).center())
        self.wait()
        self.assertEqual(self.page.route[1]["folder_id"], folder["id"])
        query = next(w for w in self.page.scroll.widget().findChildren(QLineEdit) if w.accessibleName() == "搜索当前内容")
        query.setText("客户")
        QTest.keyClick(query, Qt.Key_Return)
        self.wait()
        self.page.open_object("asset", asset["id"], asset["revision_id"])
        self.wait()
        from PySide6.QtWidgets import QComboBox
        versions = next(w for w in self.page.findChildren(QComboBox) if w.accessibleName() == "资料版本")
        self.assertEqual(versions.currentData()["id"], asset["revision_id"])
        self.shot("desktop-asset-detail")
        self.page.go_back()
        self.wait()
        self.assertEqual(self.page.route[1]["search"], "客户")
        self.assertEqual(self.page.route[1]["folder_id"], folder["id"])
        self.assertEqual(self.page.main_table.topLevelItemCount(), 1)
        for name, width, height in (("wide", 1100, 780), ("narrow", 480, 680)):
            self.page.resize(width, height)
            self.qt.processEvents()
            self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(), 0)
            self.shot("desktop-assets-" + name)

    def test_my_tasks_and_shared_results_are_separate_and_open_original_conversation(self):
        identity = self.page.client.identity
        binding = self.local.bind_workspace(identity, self.backend.space, Path(self.backend.temp.name) / "private-task")
        self.host.chat_storage.save_conversation("local-task", [], title="尚未分享的方案草稿")
        self.host.chat_storage.bind_team_space("local-task", binding)
        self.backend.publish(title="已分享的需求摘要", body="仅供团队查看的整理结果。")
        self.open_space("tasks")
        self.assertEqual(self.page.main_table.topLevelItemCount(), 1)
        self.assertIn("已分享的需求摘要", self.page.main_table.topLevelItem(0).text(0))
        self.shot("desktop-results")
        self.click("我的任务")
        self.assertEqual(self.page.main_table.topLevelItemCount(), 1)
        self.assertEqual(self.page.main_table.topLevelItem(0).text(0), "尚未分享的方案草稿")
        self.shot("desktop-my-tasks")
        tree = self.page.main_table
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(tree.topLevelItem(0)).center())
        self.wait()
        opened = []
        self.page.conversationRequested.connect(opened.append)
        self.click("继续对话")
        self.assertEqual(opened, ["local-task"])
        self.click("选择内容发布为成果")
        self.assertEqual(self.page.fields["body"].toPlainText(), "")
        self.assertEqual(len(self.backend.get(suffix="/shares")["items"]), 1)

    def test_known_withdrawal_does_not_reveal_old_body_when_returning_to_cached_detail(self):
        share = self.backend.publish(title="阶段性成果", body="撤回后不应再次显示的正文")
        self.open_space("tasks")
        self.page.open_object("share", share["id"])
        self.wait()
        self.page.open_object("snapshot", share["snapshot_id"])
        self.wait()
        self.backend.command("owner", "share.withdraw", id=share["id"], revision=1, reason="撤回")
        self.page.refresh()
        self.wait()
        self.assertFalse(any(key[0] == self.backend.space for key in self.page._cache))
        from PySide6.QtWidgets import QPlainTextEdit
        self.assertFalse(self.page.scroll.widget().findChildren(QPlainTextEdit))
        self.page.go_back()
        self.wait()
        self.assertEqual(self.page.route[1]["kind"], "share")
        self.assertFalse(self.page.scroll.widget().findChildren(QPlainTextEdit))

    def test_account_switch_during_read_discards_old_response_and_progress(self):
        plan = self.backend.command("owner", "plan.create", title="旧页面内容")
        self.open_space("plans")
        client, ticket = self.page.client, self.page.ticket
        original = client.object
        entered, release = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Object read was not released")
            return original(*args)
        with patch.object(client, "object", side_effect=delayed):
            try:
                self.page.open_object("plan", plan["id"])
                until = time.time() + 2
                while not entered.is_set() and time.time() < until:
                    QTest.qWait(10)
                self.assertTrue(entered.is_set())
                state = self.enterprise.read()
                self.enterprise.write({**state, "member": {"id": "member", "name": "新账号"}, "generation": "switched"}, self.backend.tokens["member"])
                self.page.refresh()
                self.wait()
                self.assertEqual(self.page.client.identity["member_id"], "member")
                self.assertEqual(self.page.route[0], "list")
                status = self.page.status.text()
                self.page._progress((client, ticket), "旧账号上传进度")
                self.assertEqual(self.page.status.text(), status)
            finally:
                release.set()
            QTest.qWait(200)
        self.assertEqual(self.page.route[0], "list")
        self.assertFalse(any(key[1] == "open_object" for key in self.page._cache))

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

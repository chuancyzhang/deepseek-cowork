import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtTest import QTest
from PySide6.QtCore import QThreadPool, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from core.chat_storage import ChatStorage
from core.agent_manager import get_agent_manager_registry
from core.subagent_control import SubagentControl
from ui.subagent_panel import SubagentPanel
from core.theme import ThemeRuntimeManager, default_design_tokens, DesignTokens
from core.theme_service import ThemeRepository, DEFAULT_THEME_ID
from test_subagent_reliable_runtime import Worker
from main import MainWindow


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ("msyh.ttc", "consola.ttf"):
            font = Path("C:/Windows/Fonts") / name
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
        cls.app.setFont(QFont("Microsoft YaHei", 10))

    def setUp(self):
        self.qt_errors = []
        self.previous_excepthook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: self.qt_errors.append(str(value))
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = ChatStorage(os.path.join(self.tmp.name, "chat.sqlite"))
        self.config = SimpleNamespace(get=lambda key, default=None: default, get_god_mode=lambda: False,
                                      get_chat_workspace_root=lambda: self.tmp.name)
        self.workers = []
        def factory(*args):
            worker = Worker(*args)
            self.workers.append(worker)
            return worker
        self.manager = get_agent_manager_registry().get_session_manager(
            "parent", self.storage, self.config, workspace_dir=self.tmp.name, worker_factory=factory)
        self.host = QWidget()
        self.host.resize(520, 800)
        layout = QVBoxLayout(self.host)
        self.panel = SubagentPanel(lambda item, parent, existing: MainWindow._render_subagent_conversation_item(None, item, parent, existing), self.host)
        layout.addWidget(self.panel)
        self.control = SubagentControl(self.storage, self.config)
        self.original_tokens = default_design_tokens()
        self.original_style = self.app.styleSheet()
        self.original_registry = getattr(self.app, "theme_binding_registry", None)
        self.original_manager = getattr(self.app, "theme_manager", None)
        self.theme_manager = None
        if os.environ.get("SUBAGENT_DARK"):
            self.theme_manager = ThemeRuntimeManager(self.app, ThemeRepository(self.tmp.name))
            self.app.theme_manager = self.theme_manager
            tokens = {"bg_main": "#202124", "bg_secondary": "#292b30", "text_primary": "#eeeeee",
                      "text_secondary": "#b6b8bd", "text_tertiary": "#989ba2", "border_subtle": "#42454b",
                      "separator": "#42454b", "bg_chat": "#202124", "bg_assistant_stream": "#202124",
                      "bg_user_bubble": "#353849", "bg_app": "#202124"}
            self.assertTrue(self.theme_manager.apply_profile({"id": "qa-dark", "name": "QA dark",
                "base": DEFAULT_THEME_ID, "overrides": {"tokens": tokens}}, reason="subagent-qa"), self.theme_manager.last_error)
            self.panel.apply_theme()
        self.host.show()

    def drain(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.panel.busy:
                self.app.processEvents()
                return
            time.sleep(.01)
        self.fail("panel request did not settle")

    def tearDown(self):
        self.panel.timer.stop()
        QThreadPool.globalInstance().waitForDone(5000)
        self.app.processEvents()
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()
        if self.theme_manager is not None:
            self.theme_manager.stop()
            for name, value in self.original_tokens.items():
                setattr(DesignTokens, name, value)
            self.app.setStyleSheet(self.original_style)
            self.theme_manager.deleteLater()
            self.app.processEvents()
            self.app.theme_binding_registry = self.original_registry
            self.app.theme_manager = self.original_manager
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QThreadPool.globalInstance().waitForDone(5000)
        self.tmp.cleanup()
        sys.excepthook = self.previous_excepthook
        self.assertEqual(self.qt_errors, [], "Qt callback failed")

    def seed(self):
        ids = []
        for name, prompt in (("检查登录流程", "检查登录失败后的提示与恢复入口"), ("核对测试覆盖", "检查关键路径是否缺少测试"), ("整理接口说明", "核对接口文档与当前实现")):
            ids.append(self.manager.spawn_agent(prompt, name=name)["agent_id"])
        self.workers[0].content_signal.emit("正在检查登录流程，已确认用户输入会保留。")
        self.workers[1].complete("", error="测试服务暂时不可用，已保留检查记录。")
        self.manager.send_input(ids[2], "继续核对停止与恢复接口", "queued-3")
        self.manager.interrupt_agent(ids[2], self.manager._agents[ids[2]].run_id)
        self.workers[2].complete("已核对两个接口，剩余项目等待继续处理。")
        self.panel.bind("parent", self.control)
        self.drain()
        return ids

    def test_three_tasks_failure_stop_queue_and_resume(self):
        ids = self.seed()
        self.assertEqual(len(self.panel.rows), 3)
        self.assertIn("失败", self.panel.rows[ids[1]].text())
        self.panel.open_agent(ids[2])
        self.drain()
        self.assertTrue(self.panel.resume.isVisible())
        self.assertIn("1 条待处理", self.panel.metrics.text())
        self.panel.command("resume")
        self.drain()
        self.assertEqual(len(self.workers), 4)
        self.assertEqual(self.workers[-1].messages[-1]["content"], "继续核对停止与恢复接口")

    def test_draft_retained_and_queue_saved_without_copying_to_parent(self):
        ids = self.seed()
        self.panel.open_agent(ids[0])
        self.drain()
        self.panel.editor.setPlainText("未发送草稿")
        self.panel.show_overview()
        self.drain()
        self.panel.open_agent(ids[0])
        self.drain()
        self.assertEqual(self.panel.editor.toPlainText(), "未发送草稿")
        self.panel.command("send")
        self.drain()
        self.assertEqual(self.panel.editor.toPlainText(), "")
        self.assertEqual(self.manager.list_agent_summaries()[0]["pending_count"], 1)
        self.assertEqual(self.storage.get_messages("parent"), [])

    def test_failed_send_retains_draft_and_same_message_identity(self):
        ids = self.seed()
        self.panel.open_agent(ids[0])
        self.drain()
        self.panel.editor.setPlainText("请保留这条消息")
        original = self.control.request
        def fail(conversation, operation, **data):
            if operation == "send":
                raise ConnectionError("连接中断")
            return original(conversation, operation, **data)
        self.control.request = fail
        self.panel.command("send")
        self.drain()
        identity = self.panel.views[("parent", ids[0])]["message_id"]
        self.assertEqual(self.panel.editor.toPlainText(), "请保留这条消息")
        self.control.request = original
        self.panel.command("send")
        self.drain()
        self.assertIn(identity, {r["message_id"] for r in self.storage.agent_inbox(ids[0])})

    def test_live_content_never_disappears_on_refresh_and_rows_are_reused(self):
        ids = self.seed()
        rows = dict(self.panel.rows)
        self.panel.open_agent(ids[0])
        self.drain()
        self.workers[0].content_signal.emit("正在验证恢复路径。")
        self.panel.refresh()
        self.drain()
        first = self.panel.messages[self.manager._agents[ids[0]].assistant_id]["content"]
        self.panel.refresh()
        self.drain()
        self.assertEqual(first, self.panel.messages[self.manager._agents[ids[0]].assistant_id]["content"])
        self.assertTrue(all(self.panel.rows[k] is v for k, v in rows.items()))

    def test_parent_result_deduplicates_reconnect_but_accepts_next_run(self):
        aid = self.manager.spawn_agent("task")["agent_id"]
        state = SimpleNamespace(session_id="parent", messages=[], completed_agent_result_ids=set(),
                                displayed_count=0, render_items=[])
        window = SimpleNamespace(chat_storage=self.storage,
            _rebuild_session_render_spans=lambda state: None, save_chat_history=lambda **kwargs: None)
        event = dict(agent_id=aid, run_id="first", status="completed", content="answer",
                     summon_source="mention", agent_name="检查")
        MainWindow._append_summoned_agent_result(window, state, event)
        state.completed_agent_result_ids.clear()
        MainWindow._append_summoned_agent_result(window, state, event)
        self.assertEqual(len(state.messages), 1)
        MainWindow._append_summoned_agent_result(window, state, dict(event, run_id="second"))
        self.assertEqual(len(state.messages), 2)
        self.assertNotEqual(state.messages[0]["id"], state.messages[1]["id"])

    def test_screenshots_and_parented_widgets(self):
        ids = self.seed()
        output = os.environ.get("SUBAGENT_SCREENSHOTS")
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            for width in (380, 640):
                self.host.resize(width, 820)
                self.app.processEvents()
                QTest.qWait(150)
                self.host.grab().save(str(Path(output) / f"overview-{width}.png"))
            self.panel.open_agent(ids[2])
            self.drain()
            for width in (380, 640):
                self.host.resize(width, 820)
                self.app.processEvents()
                QTest.qWait(150)
                self.host.grab().save(str(Path(output) / f"conversation-{width}.png"))
        for row in self.panel.rows.values():
            self.assertFalse(row.isWindow())
            self.assertIsNotNone(row.parentWidget())
        self.panel.show_overview()
        self.drain()
        self.host.resize(380, 820)
        summaries = list(self.panel.summaries.values())
        summaries[0] = dict(summaries[0], latest_progress="长摘要需要在小窗口内完整换行。" * 8)
        self.panel.set_summaries(summaries)
        QTest.qWait(100)
        self.assertEqual(self.panel.overview.horizontalScrollBar().maximum(), 0)


if __name__ == "__main__":
    unittest.main()

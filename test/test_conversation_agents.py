"""Expert continuity, main-message attachments and guarded final previews."""
import copy
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLabel

from core.conversation_agents import (
    HTML_AGENT_ID, HTML_IMAGE_TASK, agent_role_context, builtin_agent_profiles,
    extract_agent_mentions, message_agent_identity,
)
from core.llm.providers import OpenAIProvider
from core.conversation_integrity import canonical_ledger_message
from ui.conversation_agents import ConversationAgentsMixin
from main import AgentProfileManager, AssistantTurnGroup, MainWindow


class AgentPolicyTests(unittest.TestCase):
    def test_registry_is_independent_and_resolves_collisions(self):
        custom = [{"id": "a", "name": "HTML创作"}, {"id": "b", "name": "HTML创作（内置）"}]
        original = copy.deepcopy(custom)
        profiles = builtin_agent_profiles(custom)
        self.assertEqual(profiles[0]["name"], "HTML创作（内置 2）")
        profiles[0]["skill_names"].append("changed")
        self.assertEqual(builtin_agent_profiles()[0]["skill_names"], [])
        self.assertEqual(custom, original)

    def test_mentions_preserve_paragraphs_code_and_email(self):
        profile = builtin_agent_profiles()[0]
        body = "第一段\n\n- a  b\n- c\n```html\n<p>@HTML创作</p>\n```\n`@HTML创作` a@HTML创作"
        found, cleaned = extract_agent_mentions("@HTML创作 " + body, [profile])
        self.assertEqual(found, [profile])
        self.assertEqual(cleaned, body)

    def test_runtime_role_reset_and_output_path(self):
        profile = builtin_agent_profiles()[0]
        self.assertEqual(agent_role_context(None, None), {})
        self.assertIn("普通助手", agent_role_context({}, None)["agent_system_prompt"])
        context = agent_role_context(profile, profile, workspace="D:/work", message_id="a" * 32)
        self.assertIn("html-aaaaaaaaaaaa.html", context["agent_system_prompt"])
        self.assertEqual(context["agent_summon_source"], "conversation")
        with self.assertRaises(ValueError):
            agent_role_context(profile, None)

    def test_history_names_are_frozen_and_guidance_does_not_change_identity(self):
        old = {"id": "a", "name": "旧名字"}
        messages = [{"id": "u", "role": "user", "meta": {"conversation_agent": old, "request_id": "r"}},
                    {"id": "g", "role": "user", "meta": {"same_turn_guidance": True}},
                    {"id": "context", "role": "user", "meta": {"kind": "runtime_context", "request_id": "r"}},
                    {"id": "a", "role": "assistant", "meta": {"request_id": "r"}}]
        self.assertEqual(message_agent_identity(messages, messages[-1]), old)
        messages[-1]["meta"]["conversation_agent"] = {}
        self.assertEqual(message_agent_identity(messages, messages[-1]), {})

    def test_reply_identity_is_only_a_ui_projection(self):
        message = {"id": "a", "role": "assistant", "content": "结果"}
        projected = copy.deepcopy(message)
        projected["meta"] = {"ui_conversation_agent": {"id": "expert", "name": "专家"}}
        self.assertEqual(canonical_ledger_message(message), canonical_ledger_message(projected))


class AgentPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "page.html"
        self.path.write_text("<!doctype html><html><body>页面</body></html>", encoding="utf-8")
        self.state = SimpleNamespace(session_id="s")
        self.window = MainWindow.__new__(MainWindow)
        self.window.current_session_id = "s"
        self.window._workspace_dir_for_state = lambda state: self.temporary.name
        self.window.register_deliverable_paths = MagicMock()
        self.window.open_deliverable_from_chat = MagicMock()
        self.window._show_conversation_notice = MagicMock()
        self.profile = builtin_agent_profiles()[0]
        self.content = f"[打开页面](<{self.path}>)"

    def complete(self, run="run"):
        self.window._complete_conversation_agent(self.state, self.content, run, self.profile)

    def test_final_preview_is_deduplicated(self):
        self.complete(); self.complete()
        self.window.open_deliverable_from_chat.assert_called_once_with(str(self.path), "s")

    def test_background_registers_without_opening(self):
        self.window.current_session_id = "other"
        self.complete()
        self.window.register_deliverable_paths.assert_called_once()
        self.window.open_deliverable_from_chat.assert_not_called()
        self.assertEqual(self.window._show_conversation_notice.call_args.kwargs["action_text"], "打开页面")

    def test_dirty_and_busy_editors_preserve_edits(self):
        for edit_state, dirty in (("dirty", True), ("saving", False), ("loading", False), ("conflict", False)):
            self.window.deliverable_edit_dirty = dirty
            self.window.deliverable_edit_state = edit_state
            self.complete(edit_state)
        self.window.open_deliverable_from_chat.assert_not_called()

    def test_missing_file_warns_but_discussion_is_quiet(self):
        self.path.unlink()
        self.complete()
        self.window.open_deliverable_from_chat.assert_not_called()
        self.window._show_conversation_notice.assert_called_once()
        self.window._show_conversation_notice.reset_mock()
        self.content = "可以先讨论页面布局。"
        self.complete("discussion")
        self.window._show_conversation_notice.assert_not_called()

    def test_preview_exception_retains_file_and_manual_action(self):
        self.window.open_deliverable_from_chat.side_effect = RuntimeError("preview unavailable")
        with self.assertLogs("ui.conversation_agents", level="ERROR"):
            self.complete()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.window._show_conversation_notice.call_args.kwargs["action_text"], "打开页面")

    def test_preview_waits_for_terminal_save_revision(self):
        self.state.pending_agent_preview = {"revision": 4, "content": self.content, "run_id": "run", "profile": self.profile}
        self.window._flush_agent_preview_after_save(self.state, 3)
        self.window.open_deliverable_from_chat.assert_not_called()
        self.window._flush_agent_preview_after_save(self.state, 4)
        self.window._flush_agent_preview_after_save(self.state, 5)
        self.window.open_deliverable_from_chat.assert_called_once()

    def test_missing_primary_does_not_open_an_earlier_file(self):
        self.content += f"\n[主页面](<{self.path.parent / 'missing.html'}>)"
        self.complete()
        self.window.open_deliverable_from_chat.assert_not_called()
        self.window._show_conversation_notice.assert_called_once()


class AgentSubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cowork-expert-")
        self.env_patch = patch.dict(os.environ, {"APPDATA": self.temporary.name})
        self.env_patch.start()
        config = Path(self.temporary.name) / "DeepSeekCowork" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text("{}", encoding="utf-8")
        self.window = MainWindow()
        self.state = self.window.get_current_session()
        self.window._set_session_workspace(self.state, self.temporary.name, source="project")
        self.profile = builtin_agent_profiles()[0]
        self.model = {"id": "test-model", "provider_type": "openai", "model_name": "test", "supports_vision": True}
        self.window._model_id_for_state = lambda state: "test-model"
        self.window._model_profile_for_state = lambda *args, **kwargs: dict(self.model)
        self.window._model_profile_snapshot_for_state = lambda *args, **kwargs: dict(self.model)
        self.window._selected_reasoning_effort = lambda *args, **kwargs: ""
        self.window.queue_daemon_connection = MagicMock()
        self.window.daemon_available = False
        self.window.process_agent_logic = MagicMock()
        self.window._dispatch_agent_profiles = MagicMock(side_effect=AssertionError("must use main conversation"))
        self.window._show_conversation_notice = MagicMock(wraps=self.window._show_conversation_notice)

    def tearDown(self):
        self.state.live_activity = False
        self.state.session_status = "completed"
        self.window._dispose_application_event_filters()
        self.window.close()
        deadline = time.monotonic() + 10
        while not getattr(self.window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.window.file_model.directoryLoaded.disconnect()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.env_patch.stop()
        self.temporary.cleanup()

    def image(self, name):
        path = Path(self.temporary.name) / name
        image = QImage(40, 30, QImage.Format_RGB32)
        image.fill(0xff228855)
        self.assertTrue(image.save(str(path)))
        return str(path)

    def finish(self, content="已完成"):
        self.window.handle_llm_response({"content": content, "request_id": self.state.active_turn_request_id,
            "generated_messages": [{"id": "reply-" + self.state.active_turn_request_id, "role": "assistant", "content": content}]},
            self.state.session_id, self.state.active_turn_id)
        deadline = time.monotonic() + 5
        while self.window._session_is_busy(self.state) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.window._session_is_busy(self.state),
                         (self.state.session_status, self.window._show_conversation_notice.call_args))

    def test_images_reach_both_protocols_and_followup_uses_main_role(self):
        images = [self.image("one.png"), self.image("two.png")]
        self.assertTrue(self.window._submit_session_request(self.state, "@HTML创作", images))
        message = next(m for m in self.state.messages if m["role"] == "user")
        self.assertIn(HTML_IMAGE_TASK, message["content"])
        self.assertEqual([p["path"] for p in message["content_parts"] if p["type"] == "input_image"], images)
        provider = OpenAIProvider("test", "https://example.invalid/v1", "test", supports_vision=True)
        chat = provider._prepare_chat_completions_messages([message])[0][0]["content"]
        responses = provider._prepare_responses_input([message])[0]["content"]
        self.assertEqual(sum(p["type"] == "image_url" for p in chat), 2)
        self.assertEqual(sum(p["type"] == "input_image" for p in responses), 2)
        context = self.window.process_agent_logic.call_args.kwargs["run_context"]
        self.assertEqual(context["agent_profile_id"], HTML_AGENT_ID)
        self.assertFalse(context.get("is_subagent", False))
        self.finish()
        self.assertEqual(self.state.messages[-1]["meta"]["ui_conversation_agent"]["id"], HTML_AGENT_ID)
        self.window.save_chat_history(self.state.session_id, flush=True)
        self.assertTrue(self.window.wait_for_chat_save_revision(self.state.session_id, self.state.chat_save_revision))
        restored = next(m for m in self.window.chat_storage.get_messages(self.state.session_id) if m["id"] == message["id"])
        self.assertEqual(restored["content_parts"], message["content_parts"])
        self.assertEqual(sum(p["type"] == "input_image" for p in provider._prepare_responses_input([restored])[0]["content"]), 2)
        self.assertTrue(self.window._submit_session_request(self.state, "把标题改成绿色", []))
        self.assertEqual(self.window.process_agent_logic.call_count, 2)
        self.assertEqual(self.window.process_agent_logic.call_args.kwargs["run_context"]["agent_profile_id"], HTML_AGENT_ID)
        self.window._dispatch_agent_profiles.assert_not_called()

    def test_switch_close_and_frozen_history(self):
        custom = {"id": "custom", "name": "分析专家", "enabled": True, "system_prompt": "分析资料", "skill_names": []}
        self.window._available_agent_profiles = lambda: [self.profile, custom]
        self.window.select_conversation_agent(self.profile)
        self.assertTrue(self.window._submit_session_request(self.state, "做一个报名页面", []))
        self.assertFalse(self.window.select_conversation_agent(custom))
        self.finish()
        first_reply = self.state.messages[-1]
        self.assertTrue(self.window.select_conversation_agent(custom))
        self.assertTrue(self.window._submit_session_request(self.state, "分析页面", []))
        self.assertEqual(self.window.process_agent_logic.call_args.kwargs["run_context"]["agent_profile_id"], "custom")
        self.finish("分析完成")
        self.assertEqual(first_reply["meta"]["ui_conversation_agent"]["name"], "HTML创作")
        self.assertTrue(self.window.select_conversation_agent())
        self.assertTrue(self.window._submit_session_request(self.state, "继续讨论", []))
        context = self.window.process_agent_logic.call_args.kwargs["run_context"]
        self.assertEqual(context["agent_profile_name"], "普通助手")
        self.assertEqual(self.window._compose_session_meta(self.state)["conversation_agent"], {})

    def test_missing_role_and_nonvision_model_preserve_draft(self):
        self.state.conversation_agent = {"id": "removed", "name": "已删除"}
        self.window.input_field.setPlainText("草稿")
        self.assertFalse(self.window._submit_session_request(self.state, "草稿", [], clear_current_input=True))
        self.assertEqual(self.window.input_field.toPlainText(), "草稿")
        self.window.select_conversation_agent(self.profile)
        image = self.image("ref.png")
        self.state.prompt_files = [image]
        self.model["supports_vision"] = False
        self.window.show_model_selector_popover = MagicMock()
        self.assertFalse(self.window._submit_session_request(self.state, "草稿", [image], clear_current_input=True))
        self.assertEqual(self.window.input_field.toPlainText(), "草稿")
        self.assertEqual(self.state.prompt_files, [image])
        self.window.process_agent_logic.assert_not_called()

    def test_empty_mention_preserves_input_and_selects_role(self):
        self.assertFalse(self.window._submit_session_request(self.state, "@HTML创作", []))
        self.assertEqual(self.state.conversation_agent["id"], HTML_AGENT_ID)
        self.window.process_agent_logic.assert_not_called()

    def test_failed_retry_keeps_original_expert_and_attachments(self):
        self.window.select_conversation_agent(self.profile)
        image = self.image("retry.png")
        self.assertTrue(self.window._submit_session_request(self.state, "按图片制作页面", [image]))
        run_id = self.state.active_turn_request_id
        self.window.open_deliverable_from_chat = MagicMock()
        self.window.handle_llm_response({"error": "request timed out", "request_id": run_id},
                                        self.state.session_id, self.state.active_turn_id)
        deadline = time.monotonic() + 5
        while self.window._session_is_busy(self.state) and time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(0.01)
        self.window.open_deliverable_from_chat.assert_not_called()
        self.assertTrue(self.window.select_conversation_agent())
        self.assertTrue(self.window.retry_failed_run(self.state.session_id, run_id))
        context = self.window.process_agent_logic.call_args.kwargs["run_context"]
        self.assertEqual(context["agent_profile_id"], HTML_AGENT_ID)
        users = [message for message in self.state.messages if message["role"] == "user"]
        self.assertEqual(len(users), 2)
        self.assertNotEqual(users[0]["id"], users[1]["id"])
        self.assertEqual([part["path"] for part in users[1]["content_parts"] if part["type"] == "input_image"], [image])

    def test_builtin_management_is_readonly_and_not_persisted(self):
        manager = AgentProfileManager([], lambda: [])
        manager.open_profile_editor()
        self.assertTrue(manager.system_prompt_edit.isReadOnly())
        self.assertFalse(manager.delete_profile_btn.isEnabled())
        self.assertEqual(manager.get_profiles(), [])
        manager.copy_profile()
        self.assertFalse(manager.system_prompt_edit.isReadOnly())
        self.assertEqual(len(manager.get_profiles()), 1)
        self.assertFalse(manager.get_profiles()[0].get("builtin", False))
        manager.deleteLater()

    def test_saved_role_restores_with_history_without_auto_preview(self):
        self.window.select_conversation_agent(self.profile)
        self.assertTrue(self.window._submit_session_request(self.state, "讨论布局", []))
        self.finish("使用两列布局")
        self.assertTrue(self.window.save_chat_history(self.state.session_id, flush=True))
        sid = self.state.session_id
        self.assertTrue(self.window.wait_for_chat_save_revision(sid, self.state.chat_save_revision))
        stored = self.window.chat_storage.get_messages(sid)
        meta = self.window.chat_storage.get_conversation_meta(sid)
        self.assertEqual(meta["conversation_agent"]["id"], HTML_AGENT_ID)
        self.window.open_deliverable_from_chat = MagicMock()
        self.state.conversation_agent = None
        self.window._handle_session_history_loaded({"ok": True, "session_id": sid,
            "token": self.state.history_load_token, "messages": stored + [
                {"id": "hidden-context", "role": "user", "content": "hidden", "meta": {"kind": "runtime_context"}}], "conversation_meta": meta,
            "conversation_record": self.window.chat_storage.get_conversation_record(sid)})
        deadline = time.monotonic() + 5
        while getattr(self.state, "history_initial_render_completion", None) is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        # Initial history rendering yields between spans.
        for _ in range(10):
            self.app.processEvents()
        self.assertEqual(self.state.conversation_agent["id"], HTML_AGENT_ID)
        self.assertEqual(self.state.active_turn_agent["id"], HTML_AGENT_ID)
        labels = self.state.chat_layout.parentWidget().findChildren(QLabel, "ConversationAgentName")
        self.assertTrue(any(label.text() == "HTML创作" for label in labels))
        self.window.open_deliverable_from_chat.assert_not_called()
        other = self.window.create_new_session()
        self.assertIsNone(self.window.get_session(other).conversation_agent)
        self.window.set_current_session(sid)
        self.assertEqual(self.window.agent_picker_btn.text(), "HTML创作")


if __name__ == "__main__":
    unittest.main()

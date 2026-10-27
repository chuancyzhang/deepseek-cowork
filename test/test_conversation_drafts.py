import os
import tempfile
import unittest
from unittest.mock import patch

from core.chat_storage import ChatStorage
from core.conversation_drafts import history_record_has_content, history_record_title, saved_composer


class ConversationDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = ChatStorage(os.path.join(self.temp.name, "history.sqlite"))

    def test_legacy_empty_update_record_is_hidden_without_deleting_real_drafts(self):
        cases = {
            "pristine": {"grill_mode_state": "disabled", "run_phase": "待开始",
                         "update_composer": {"text": "", "files": []}},
            "text": {"update_composer": {"text": "待办事项", "files": []}},
            "attachment": {"composer_draft": {"text": "", "files": ["report.pdf"]}},
            "references": {"composer_conversation_refs": [{"session_id": "other", "title": "参考"}]},
            "skills": {"selected_skill_names": ["report"]},
            "manual": {"manual_title": True},
            "recovery": {"run_phase": "Interrupted"},
        }
        for identity, meta in cases.items():
            self.storage.upsert_conversation(identity, title="新任务", meta=meta)
        records = self.storage.list_conversation_summaries()
        self.assertEqual({r["id"] for r in records if history_record_has_content(r)}, set(cases) - {"pristine"})
        self.assertIsNotNone(self.storage.get_conversation_record("pristine"))
        self.assertEqual(history_record_title(next(r for r in records if r["id"] == "text")), "草稿 · 待办事项")

    def test_repeated_checkpoint_does_not_promote_draft_but_user_edit_does(self):
        meta = {"composer_draft": {"text": "草稿", "files": []}, "history_save_revision": 1}
        with patch("core.chat_storage.time.time", return_value=100):
            self.storage.save_conversation_safely("draft", [], title="新任务", status="draft", meta=meta)
        meta["history_save_revision"] = 2
        with patch("core.chat_storage.time.time", return_value=200):
            self.storage.save_conversation_safely("draft", [], title="新任务", status="draft", meta=meta)
        self.assertEqual(self.storage.get_conversation_record("draft")["updated_at"], 100)
        self.assertEqual(self.storage.get_conversation_meta("draft")["history_save_revision"], 2)
        meta["composer_draft"]["text"] = "修改后的草稿"
        with patch("core.chat_storage.time.time", return_value=300):
            self.storage.save_conversation_safely("draft", [], title="新任务", status="draft", meta=meta)
        self.assertEqual(self.storage.get_conversation_record("draft")["updated_at"], 300)

    def test_new_message_advances_activity_even_with_identical_metadata(self):
        messages = [{"id": "u1", "role": "user", "content": "问题"}]
        with patch("core.chat_storage.time.time", return_value=100):
            self.storage.save_conversation_safely("chat", messages, title="问题")
        with patch("core.chat_storage.time.time", return_value=200):
            self.storage.save_conversation_safely("chat", messages, title="问题")
        self.assertEqual(self.storage.get_conversation_record("chat")["updated_at"], 100)
        messages.append({"id": "a1", "role": "assistant", "content": "答复"})
        with patch("core.chat_storage.time.time", return_value=300):
            self.storage.save_conversation_safely("chat", messages, title="问题")
        self.assertEqual(self.storage.get_conversation_record("chat")["updated_at"], 300)
        self.assertTrue(self.storage.list_conversation_summaries()[0]["has_messages"])

    def test_cleared_canonical_draft_cannot_fall_back_to_old_update_text(self):
        self.assertEqual(saved_composer({"composer_draft": {}, "update_composer": {"text": "旧输入"}}), {})

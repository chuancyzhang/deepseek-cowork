import copy
import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from core.chat_save_queue import ChatSaveRequest, ChatSaveWorker, ContextSaveRequest
from core.chat_storage import ChatStorage
from core.context_storage import source_fingerprints
from core.context_window import ContextWindowManager
from core.long_context import (ContextBudget, ContextCancelled, ContextError, desktop_policy,
                               estimate_request, format_capacity, normalize_policy, validate_checkpoint)
from core.runtime_journal import RuntimeJournal


def checkpoint(refs=(), goal="finish the corrected task"):
    return {"goal": goal, "constraints": ["do not repeat completed operations"],
            "completed": [], "decisions": [], "pending": [], "next_steps": [], "refs": list(refs)}


class ContextFixture(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = ChatStorage(os.path.join(self.temp.name, "chat_history.sqlite"))
        self.journal = RuntimeJournal(self.temp.name)
        self.messages = [{"id": "user-0", "role": "user", "content": "finish the task, never send email"}]
        self.storage.save_conversation_safely("s", self.messages)
        self.journal.begin_run("s", "run", writer_owner="ui:test", base_messages=self.messages)

    def payload(self, window="w1", previous="", messages=None):
        messages = messages or self.messages
        return {"id": window, "previous_id": previous, "run_id": "run", "history_revision": 0,
                "source_fingerprints": source_fingerprints(messages),
                "covered_ids": [m["id"] for m in messages], "content": checkpoint(),
                "seed_messages": [{"role": "user", "content": "checkpoint"}],
                "runtime_state": {"session_id": "s"}, "expires_at": time.time() + 10}


class TestContextStorage(ContextFixture):
    def test_atomic_cas_idempotency_and_restart(self):
        candidate = self.payload()
        self.storage.commit_context_window("s", candidate)
        self.assertEqual(self.storage.commit_context_window("s", candidate)["outcome"], "unchanged")
        with self.assertRaises(ValueError):
            self.storage.commit_context_window("s", self.payload("stale"))
        self.storage.commit_context_window("s", self.payload("w2", "w1"))
        reopened = ChatStorage(self.storage.db_path)
        self.assertEqual(reopened.get_context_window("s")["id"], "w2")
        self.assertEqual(reopened.get_context_window("s", "w1")["status"], "closed")
        self.assertEqual(len(reopened.get_messages("s")), 1)

    def test_failed_checkpoint_insert_rolls_back_old_window(self):
        self.storage.commit_context_window("s", self.payload())
        with self.storage._connect() as conn:
            conn.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON context_checkpoints BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(Exception):
            self.storage.commit_context_window("s", self.payload("w2", "w1"))
        self.assertEqual(self.storage.get_context_window("s")["id"], "w1")
        self.assertIsNone(self.storage.get_context_window("s", "w2"))

    def test_source_change_expiry_and_other_session_rejected(self):
        candidate = self.payload()
        candidate["source_fingerprints"][0]["hash"] = "wrong"
        with self.assertRaises(ValueError):
            self.storage.commit_context_window("s", candidate)
        candidate = self.payload()
        candidate["expires_at"] = 0
        with self.assertRaises(ValueError):
            self.storage.commit_context_window("s", candidate)
        self.storage.save_conversation_safely("other", [{"id": "other-user", "role": "user", "content": "private"}])
        with self.assertRaises(ValueError):
            self.storage.commit_context_window("other", self.payload())
        with self.assertRaises(ValueError):
            self.storage.context_history_read("other", "user-0")

    def test_append_during_summary_keeps_boundary_valid(self):
        candidate = self.payload()
        self.storage.save_conversation_safely("s", self.messages + [{"id": "guidance", "role": "user", "content": "cancel email, write a draft only"}])
        self.storage.commit_context_window("s", candidate)
        self.assertNotIn("guidance", self.storage.get_context_window("s")["covered_ids"])

    def test_rewrite_invalidates_descendants_and_branch_has_own_window(self):
        self.storage.commit_context_window("s", self.payload())
        messages = self.messages + [{"id": "a1", "role": "assistant", "content": "evidence"}]
        self.storage.save_conversation_safely("s", messages)
        self.storage.commit_context_window("s", self.payload("w2", "w1", messages))
        saved = self.storage.get_messages("s")
        changed = [saved[0], {**saved[1], "content": "changed evidence"}]
        self.storage.rewrite_conversation_safely("s", changed,
            expected_messages_hash=RuntimeJournal.messages_hash(saved), expected_revision=0)
        self.assertEqual(self.storage.get_context_window("s")["id"], "w1")
        self.assertEqual(self.storage.get_context_window("s", "w2")["status"], "invalid")
        self.assertEqual(self.storage.context_revision("s"), 1)
        self.storage.save_conversation_safely("branch", [{"id": "branch-u", "role": "user", "content": "branch"}])
        self.assertIsNone(self.storage.get_context_window("branch"))

    def test_literal_search_chinese_pages_and_no_reasoning(self):
        messages = self.messages + [
            {"id": "a1", "role": "assistant", "content": "早期证据 中文资料\n" * 3000,
             "reasoning_content": "private_thought"},
            {"id": "a2", "role": "assistant", "content": "第二条中文资料"},
        ]
        self.storage.save_conversation_safely("s", messages)
        first = self.storage.context_history_search("s", "中文资料", limit=1)
        self.assertEqual(first["matches"][0]["id"], "a1")
        second = self.storage.context_history_search("s", "中文资料", cursor=first["next_cursor"], limit=1)
        self.assertEqual(second["matches"][0]["id"], "a2")
        self.assertEqual(self.storage.context_history_search("s", '" OR *')["matches"], [])
        self.assertEqual(self.storage.context_history_search("s", "private_thought")["matches"], [])
        page = self.storage.context_history_read("s", "a1", length=99999)
        self.assertEqual(len(page["content"]), 12000)
        self.assertEqual(page["next_offset"], 12000)
        self.assertNotIn("reasoning_content", page)

    def test_reordering_source_invalidates_checkpoint(self):
        messages = self.messages + [{"id": "u2", "role": "user", "content": "new requirement"}]
        self.storage.save_conversation_safely("s", messages)
        self.storage.commit_context_window("s", self.payload(messages=messages))
        saved = self.storage.get_messages("s")
        self.storage.rewrite_conversation_safely("s", list(reversed(saved)),
            expected_messages_hash=RuntimeJournal.messages_hash(saved), expected_revision=0)
        self.assertIsNone(self.storage.get_context_window("s"))

    def test_disabling_existing_window_rejects_oversized_full_history(self):
        from core.agent import LLMWorker
        self.storage.commit_context_window("s", self.payload())
        worker = SimpleNamespace(long_context_policy=normalize_policy({}),
            chat_storage=self.storage, conversation_id="s", tools=[],
            run_context={"selected_model_profile": {"context_window_tokens": 32000}})
        with self.assertRaisesRegex(ContextError, "重新启用"):
            LLMWorker._check_disabled_context_capacity(worker,
                [{"role": "user", "content": "large history " * 50000}])
        LLMWorker._check_disabled_context_capacity(worker, self.messages)
        self.assertEqual(self.storage.get_context_window("s")["id"], "w1")
        self.assertEqual(len(self.storage.get_messages("s")), 1)


class TestContextSaveQueue(ContextFixture):
    def test_prepare_barrier_serializes_noncoalescing_commit(self):
        worker = ChatSaveWorker(self.storage.db_path, debounce_ms=10000)
        worker.start()
        try:
            messages = self.messages + [{"id": "a1", "role": "assistant", "content": "completed result"}]
            worker.enqueue(ChatSaveRequest("s", messages, "title", "running", {}, 0, 1))
            candidate = self.payload(messages=messages)
            for kind, operation in (("prepare", "p"), ("commit", "c")):
                self.journal.update_run("s", "run", {"context_operation": {"id": operation, "status": "pending"}})
                worker.enqueue_context(ContextSaveRequest("s", "run", operation, kind, candidate, time.time() + 5))
                self.assertTrue(worker.flush("s", 2000))
                self.assertTrue(self.journal.get_run("s", "run")["context_receipt"]["ok"])
            self.assertEqual(self.storage.get_context_window("s")["id"], "w1")
            self.assertEqual(len(self.storage.get_messages("s")), 2)
        finally:
            worker.stop_worker(2000)

    def test_stop_prevents_late_commit_and_receipt_is_explicit(self):
        worker = ChatSaveWorker(self.storage.db_path)
        self.journal.update_run("s", "run", {"context_operation": {"id": "c", "status": "pending"}})
        self.journal.interrupt_run("s", "run")
        worker._write_context(self.storage, ContextSaveRequest("s", "run", "c", "commit", self.payload(), time.time() + 5))
        self.assertFalse(self.journal.get_run("s", "run")["context_receipt"]["ok"])
        self.assertIsNone(self.storage.get_context_window("s"))


class TestContextManager(ContextFixture):
    def make_manager(self):
        self.events, self.summary_requests = [], []
        profile = {"context_window_tokens": 32000, "max_output_tokens": 4096}
        self.queue = ChatSaveWorker(self.storage.db_path, debounce_ms=10000)
        self.queue.start()
        self.addCleanup(lambda: self.queue.stop_worker(2000))
        self.worker = SimpleNamespace(chat_storage=self.storage, runtime_journal=self.journal,
            conversation_id="s", request_id="run", workspace_dir=self.temp.name,
            run_context={"selected_model_profile": profile}, config_manager=None, is_stopped=False,
            messages=copy.deepcopy(self.messages))
        self.revision = 0
        def event(value):
            self.events.append(copy.deepcopy(value))
            if value["type"] != "context_storage_request":
                return
            if value["operation"] == "prepare":
                known = {m["id"] for m in self.messages}
                self.messages.extend(m for m in value["generated_messages"] if m["id"] not in known)
                self.revision += 1
                self.queue.enqueue(ChatSaveRequest("s", copy.deepcopy(self.messages), "", "running", {}, 0, self.revision))
            self.queue.enqueue_context(ContextSaveRequest("s", "run", value["operation_id"],
                value["operation"], value["payload"], value["expires_at"]))
        self.worker.observability_signal = SimpleNamespace(emit=event)
        def summarize(provider, request, rollover_id, part, repair):
            self.summary_requests.append(copy.deepcopy(request))
            previous = json.loads(request[1]["content"])["previous_checkpoint"]
            result = previous or checkpoint(["user-0"])
            if "Produce JSON only" in request[1]["content"]:
                result["constraints"] = ["Produce JSON only; old format revoked"]
            return json.dumps(result, ensure_ascii=False)
        self.worker._generate_context_checkpoint = summarize
        self.manager = ContextWindowManager(self.worker, normalize_policy({"enabled": True}))
        return self.manager

    def test_four_windows_keep_goal_new_requirements_and_full_history(self):
        manager = self.make_manager()
        original = copy.deepcopy(self.messages)
        current = [{"role": "system", "content": "stable system"}, *copy.deepcopy(self.messages)]
        generated = []
        for window in range(4):
            if window == 2:
                correction = {"id": "correction", "role": "user", "content": "Revoke the old format. Produce JSON only."}
                current.append(correction)
                generated.append(correction)
            for i in range(12):
                message = {"id": f"a{window}-{i}", "role": "assistant", "content": "old evidence " * 900}
                current.append(message)
                generated.append(message)
            projected, changed = manager.prepare(current, generated, [], None)
            self.assertTrue(changed)
            self.assertLess(estimate_request(projected, []), manager.budget.trigger_tokens)
            self.assertEqual(projected[0]["content"], "stable system")
            self.assertEqual(manager.window["ordinal"], window + 1)
            if window == 2:
                self.assertIn(correction, projected)
            if window >= 2:
                self.assertIn("Produce JSON only", str(manager.window["content"]))
            before = copy.deepcopy(projected)
            current.append({"id": f"next{window}", "role": "user", "content": "continue"})
            generated.append(current[-1])
            projected, changed = manager.prepare(current, generated, [], None)
            self.assertFalse(changed)
            self.assertEqual(projected[:len(before)], before)
        self.assertEqual(self.storage.get_messages("s")[0]["content"], original[0]["content"])
        self.assertIn("old evidence", self.storage.context_history_read("s", "a0-0")["content"])
        self.assertGreater(len(self.storage.get_messages("s")), 40)
        reopened = ContextWindowManager(self.worker, normalize_policy({"enabled": True}))
        self.assertEqual(reopened.window["id"], manager.window["id"])
        self.assertTrue(any(json.loads(r[1]["content"])["previous_checkpoint"] for r in self.summary_requests))

    def test_large_tool_result_saved_and_excerpt_stays_append_only(self):
        manager = self.make_manager()
        call = {"id": "a", "role": "assistant", "content": "", "tool_calls": [{
            "id": "call", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}
        result = {"id": "t", "role": "tool", "tool_call_id": "call", "content": "evidence " * 20000}
        messages = [{"role": "system", "content": "system"}, *self.messages, call, result]
        projected, changed = manager.prepare(messages, [call, result], [], None)
        self.assertFalse(changed)
        self.assertLess(len(projected[-1]["content"]), len(result["content"]))
        self.assertIn("history_read", projected[-1]["content"])
        self.assertEqual(self.storage.context_history_read("s", "t")["total_chars"], len(result["content"]))
        again, _ = manager.prepare(messages + [{"id": "next", "role": "user", "content": "continue"}], [call, result], [], None)
        self.assertEqual(again[:len(projected)], projected)
        self.assertEqual(messages[-1], result)

    def test_invalid_checkpoint_repairs_once_without_commit(self):
        manager = self.make_manager()
        self.worker._generate_context_checkpoint = lambda *args: "invalid"
        current = [{"role": "system", "content": "sys"}, *self.messages,
                   {"id": "a1", "role": "assistant", "content": "old " * 30000},
                   {"id": "a2", "role": "assistant", "content": "recent"}]
        with self.assertRaises(ContextError):
            manager._prepare_once(current, current[2:], [], None)
        self.assertIsNone(self.storage.get_context_window("s"))
        self.assertEqual(self.journal.get_run("s", "run")["context_rollover"]["phase"], "failed")

    def test_explicit_retry_keeps_same_run_and_stopping_wait_does_not_commit(self):
        manager = self.make_manager()
        original_event = self.worker.observability_signal.emit
        def stop_on_failure(event):
            original_event(event)
            if event.get("phase") == "failed":
                self.worker.is_stopped = True
        self.worker.observability_signal.emit = stop_on_failure
        self.worker._generate_context_checkpoint = lambda *args: "invalid"
        current = [{"role": "system", "content": "sys"}, *self.messages,
                   {"id": "a1", "role": "assistant", "content": "old " * 30000},
                   {"id": "a2", "role": "assistant", "content": "recent"}]
        with self.assertRaises(ContextCancelled):
            manager.prepare(current, current[2:], [], None)
        self.assertIsNone(self.storage.get_context_window("s"))
        self.assertEqual(self.journal.get_run("s", "run")["context_rollover"]["phase"], "cancelled")

    def test_lost_commit_receipt_reuses_durable_seed_without_resummarizing(self):
        manager = self.make_manager()
        original_operation = manager._storage_operation
        original_event = self.worker.observability_signal.emit
        def operation(kind, payload, generated=None):
            result = original_operation(kind, payload, generated)
            if kind == "commit":
                raise ContextError("receipt lost after SQLite commit")
            return result
        def retry(event):
            original_event(event)
            if event.get("phase") == "failed":
                self.journal.update_run("s", "run", {"context_retry": event["rollover_id"]})
        manager._storage_operation = operation
        self.worker.observability_signal.emit = retry
        current = [{"role": "system", "content": "sys"}, *self.messages,
                   {"id": "a1", "role": "assistant", "content": "old " * 30000},
                   {"id": "a2", "role": "assistant", "content": "recent"}]
        projected, switched = manager.prepare(current, current[2:], [], None)
        self.assertTrue(switched)
        self.assertEqual(manager.window["ordinal"], 1)
        self.assertEqual(manager._project(current), projected)
        self.assertEqual(len([e for e in self.events if e.get("recovered_commit")]), 1)
        self.assertEqual(len([e for e in self.events if e.get("operation") == "commit"]), 1)

    def test_oversized_current_request_does_not_start_summary(self):
        manager = self.make_manager()
        current = [{"role": "system", "content": "sys"}, *self.messages,
                   {"id": "big", "role": "user", "content": "current input " * 20000}]
        with self.assertRaises(ContextError):
            manager._prepare_once(current, current[2:], [], None)
        self.assertEqual(self.summary_requests, [])
        self.assertIsNone(self.storage.get_context_window("s"))


class TestContextBudget(unittest.TestCase):
    def test_default_policy_and_capacity_limits(self):
        self.assertEqual(normalize_policy(None), {"enabled": False, "target_tokens": 300000})
        self.assertEqual(normalize_policy({"enabled": True, "target_tokens": 1})["target_tokens"], 300000)
        policy = normalize_policy({"enabled": True, "target_tokens": 1000000})
        self.assertFalse(desktop_policy({"experimental_long_context": policy}, excluded=True)["enabled"])
        budget = ContextBudget.from_profile({"context_window_tokens": 128000}, policy)
        self.assertEqual(budget.effective_tokens, 128000 - 8192 - 6400)
        self.assertLess(budget.trigger_tokens, budget.effective_tokens)
        with self.assertRaises(ContextError):
            ContextBudget.from_profile({}, policy)
        with self.assertRaises(ContextError):
            estimate_request([{"role": "user", "content_parts": [{"type": "image"}]}], [])

    def test_checkpoint_refs_and_length(self):
        self.assertEqual(validate_checkpoint(json.dumps(checkpoint(["m"])), {"m"})["refs"], ["m"])
        with self.assertRaises(ContextError):
            validate_checkpoint(json.dumps(checkpoint(["other"])), {"m"})
        with self.assertRaises(ContextError):
            validate_checkpoint(json.dumps(checkpoint(goal="goal " * 9000)), set())

    def test_capacity_unknown_snapshot_and_frozen_policy(self):
        self.assertEqual(format_capacity({}, policy={"enabled": True})["value"], "待统计")
        self.assertFalse(format_capacity({}, policy={})["visible"])
        budget = ContextBudget.from_profile({"context_window_tokens": 128000}, normalize_policy({"enabled": True}))
        snap = {**budget.snapshot(30000), "run_id": "run"}
        overview = format_capacity(snap, policy={"enabled": True, "target_tokens": 1000000}, active_run_id="run")
        self.assertIn("新设置下一轮生效", overview["note"])
        self.assertIn("受当前模型", overview["note"])
        self.assertEqual(sum(value for value, _ in overview["segments"]), budget.effective_tokens)
        self.assertIn("上次运行", format_capacity(snap)["note"])


if __name__ == "__main__":
    unittest.main()

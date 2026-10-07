import os
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from core.agent_manager import SessionAgentManager, get_agent_manager_registry
from core.chat_storage import ChatStorage
from core.subagent_control import SubagentControl, dispatch_subagent


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback, *_):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class Worker:
    def __init__(self, messages, *_):
        self.messages = messages
        self.running = False
        self.stopped = False
        for name in ("finished", "finished_signal", "content_signal", "thinking_signal", "tool_call_signal", "tool_result_signal", "observability_signal"):
            setattr(self, name, Signal())

    def start(self):
        self.running = True

    def stop(self):
        self.stopped = True

    def isRunning(self):
        return self.running

    def deleteLater(self):
        pass

    def complete(self, text="result", error="", release=True):
        self.finished_signal.emit({"content": text, "error": error, "generated_messages": [
            {"id": "provider-message", "role": "assistant", "content": text}]})
        if release:
            self.running = False
            self.finished.emit()


class ReliableSubagentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = ChatStorage(os.path.join(self.tmp.name, "chat.sqlite"))
        self.config = types.SimpleNamespace(get=lambda key, default=None: default, get_god_mode=lambda: False,
                                            get_chat_workspace_root=lambda: self.tmp.name)
        self.workers = []
        def factory(*args):
            worker = Worker(*args)
            self.workers.append(worker)
            return worker
        self.factory = factory
        self.manager = SessionAgentManager("parent", self.storage, self.config,
                                           workspace_dir=self.tmp.name, worker_factory=factory)

    def tearDown(self):
        self.tmp.cleanup()

    def spawn(self, message="first"):
        return self.manager.spawn_agent(message)["agent_id"]

    def test_admission_is_atomic_and_duplicate_id_is_idempotent(self):
        aid = self.spawn()
        with patch.object(self.storage, "_insert_message_row", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.manager.send_input(aid, "second", "input-2")
        self.assertEqual(len(self.storage.agent_inbox(aid)), 1)
        self.manager.send_input(aid, "second", "input-2")
        self.manager.send_input(aid, "second", "input-2")
        self.assertEqual(len(self.storage.agent_inbox(aid)), 2)
        with self.assertRaises(ValueError):
            self.manager.send_input(aid, "changed", "input-2")

    def test_fifo_context_excludes_pending_input_and_keeps_display_positions(self):
        aid = self.spawn()
        self.manager.send_input(aid, "second", "u2")
        self.manager.send_input(aid, "third", "u3")
        self.assertEqual([m["content"] for m in self.workers[0].messages], ["first"])
        before = [m["id"] for m in self.storage.get_agent_messages(aid)]
        self.workers[0].complete("answer one")
        self.assertEqual([m["content"] for m in self.workers[1].messages], ["first", "answer one", "second"])
        after = [m["id"] for m in self.storage.get_agent_messages(aid)]
        self.assertEqual(after[:len(before)], before)
        self.workers[1].complete("answer two")
        self.assertEqual([m["content"] for m in self.workers[2].messages], ["first", "answer one", "second", "answer two", "third"])
        self.assertEqual(self.manager.list_agent_summaries()[0]["status"], "running")

    def test_stop_retains_queue_and_rejects_stale_run(self):
        aid = self.spawn()
        old_run = self.manager._agents[aid].run_id
        self.workers[0].content_signal.emit("partial")
        self.manager.send_input(aid, "next", "u2")
        self.manager.interrupt_agent(aid, old_run)
        self.workers[0].complete("partial")
        self.assertEqual(len(self.workers), 1)
        row = self.manager.list_agent_summaries()[0]
        self.assertEqual((row["status"], row["pending_count"]), ("interrupted", 1))
        self.manager.resume_agent(aid)
        self.assertEqual(len(self.workers), 2)
        with self.assertRaises(ValueError):
            self.manager.interrupt_agent(aid, old_run)
        self.workers[0].content_signal.emit("stale output")
        self.assertNotIn("stale output", self.manager._agents[aid].draft_content)

    def test_restart_recovers_partial_and_never_replays_claimed_tool_work(self):
        aid = self.spawn()
        self.workers[0].content_signal.emit("kept")
        self.manager.send_input(aid, "queued", "u2")
        restored = SessionAgentManager("parent", self.storage, self.config, workspace_dir=self.tmp.name, worker_factory=self.factory)
        summary = restored.list_agent_summaries()[0]
        self.assertEqual(summary["status"], "interrupted")
        self.assertEqual(len(self.workers), 1)
        self.assertTrue(any(m["content"] == "kept" for m in self.storage.get_agent_messages(aid)))
        restored.resume_agent(aid)
        self.assertEqual([m["content"] for m in self.workers[-1].messages], ["first", "kept", "queued"])

    def test_completion_waits_for_native_thread_release(self):
        aid = self.spawn()
        self.manager.send_input(aid, "next", "u2")
        self.workers[0].complete(release=False)
        self.assertEqual(len(self.workers), 1)
        self.workers[0].running = False
        self.workers[0].finished.emit()
        self.assertEqual(len(self.workers), 2)

    def test_concurrency_limit_keeps_waiting_input_and_schedules_after_release(self):
        self.manager.max_live_agents = 1
        first = self.spawn()
        second = self.spawn("other")
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.manager._agents[second].status, "queued")
        self.workers[0].complete()
        self.assertEqual(len(self.workers), 2)
        self.assertEqual(self.workers[1].messages[0]["content"], "other")

    def test_persistence_failure_stops_without_publishing_unstored_delta(self):
        aid = self.spawn()
        with patch.object(self.manager.journal, "append_event", side_effect=OSError("disk full")):
            self.workers[0].content_signal.emit("not saved")
        self.assertTrue(self.workers[0].stopped)
        self.assertTrue(self.manager._agents[aid].paused)
        self.assertEqual(self.manager._agents[aid].draft_content, "")

    def test_exact_child_address_enforced(self):
        aid = self.spawn()
        other = SessionAgentManager("another", self.storage, self.config)
        with self.assertRaises(ValueError):
            dispatch_subagent(other, "send", {"agent_id": aid, "message_id": "bad", "content": "wrong"})
        with self.assertRaises(ValueError):
            dispatch_subagent(self.manager, "history", {"agent_id": "name"})
        self.workers[0].complete("done")
        run_id = self.manager._agents[aid].run_id
        self.manager.close_agent(aid)
        with self.assertRaises(ValueError):
            self.manager.interrupt_agent(aid, run_id)
        self.assertEqual(self.manager._agents[aid].status, "closed")

    def test_final_save_failure_releases_worker_and_recovers_without_reexecution(self):
        aid = self.spawn()
        self.manager.send_input(aid, "next", "u2")
        self.workers[0].content_signal.emit("already visible")
        with patch.object(self.storage, "merge_agent_messages", side_effect=OSError("disk full")):
            self.workers[0].complete("already visible")
        record = self.manager._agents[aid]
        self.assertIsNone(record.worker)
        self.assertTrue(record.paused)
        self.assertIsNotNone(record.final_result)
        self.manager.resume_agent(aid)
        self.assertEqual(len(self.workers), 2)
        self.assertEqual(self.workers[1].messages[-1]["content"], "next")
        messages = self.storage.get_agent_messages(aid)
        self.assertEqual(sum(m.get("content") == "already visible" for m in messages), 1)

    def test_concurrent_input_and_result_do_not_overwrite_each_other(self):
        aid = self.spawn()
        gate = threading.Barrier(3)
        errors = []
        def send():
            try:
                gate.wait()
                self.manager.send_input(aid, "concurrent", "u2")
            except Exception as exc:
                errors.append(exc)
        def finish():
            try:
                gate.wait()
                self.workers[0].complete("finished")
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=send), threading.Thread(target=finish)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(3)
        self.assertEqual(errors, [])
        contents = [m["content"] for m in self.storage.get_agent_messages(aid)]
        self.assertEqual(contents.count("concurrent"), 1)
        self.assertEqual(contents.count("finished"), 1)

    def test_empty_final_keeps_streamed_output(self):
        aid = self.spawn()
        self.workers[0].content_signal.emit("visible partial")
        self.workers[0].complete("", error="provider failed")
        self.assertIn("visible partial", [m["content"] for m in self.storage.get_agent_messages(aid)])

    def test_legacy_history_is_read_only_and_not_rewritten_by_browsing(self):
        self.storage.upsert_conversation("parent", title="legacy")
        self.storage.upsert_agent("old", "parent", status="completed")
        self.storage.replace_agent_messages("old", [{"id": "old-u", "role": "user", "content": "old input"}])
        before = self.storage.get_agent("old")
        control = SubagentControl(self.storage, self.config)
        self.assertTrue(control.request("parent", "list")[0]["read_only"])
        page = control.request("parent", "history", agent_id="old")
        self.assertEqual(page["messages"][0]["content"], "old input")
        self.assertEqual(self.storage.get_agent("old"), before)
        with self.assertRaises(ValueError):
            control.request("parent", "send", agent_id="old", message_id="new", content="new")


    def test_usage_deduplicates_requests_without_double_counting_cache(self):
        aid = self.spawn()
        usage = {"type": "llm_usage", "usage": {"request_id": "r1", "prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 80}}
        self.workers[0].observability_signal.emit(usage)
        self.workers[0].observability_signal.emit(usage)
        self.assertEqual(self.manager.list_agent_summaries()[0]["usage"], {"input_tokens": 100, "output_tokens": 20})

    def test_history_pagination_and_tool_events_survive_interruption(self):
        aid = self.spawn()
        self.workers[0].tool_call_signal.emit({"id": "same", "name": "read", "args": {"path": "a"}})
        self.workers[0].tool_result_signal.emit({"id": "same", "name": "read", "result": "ok"})
        self.manager.send_input(aid, "next", "u2")
        self.manager.interrupt_agent(aid, self.manager._agents[aid].run_id)
        self.workers[0].complete("partial")
        page = self.manager.history(aid, limit=1)
        self.assertEqual(len(page["messages"]), 1)
        self.assertIsNotNone(page["before"])
        older = self.manager.history(aid, before=page["before"])
        self.assertEqual(len(older["activity"]), 2)
        other = self.manager.spawn_agent("independent")["agent_id"]
        self.workers[-1].tool_call_signal.emit({"id": "same", "name": "other", "args": {}})
        self.assertEqual({e["payload"]["agent_id"] for e in self.manager.history(other)["activity"]}, {other})
        self.assertEqual({e["payload"]["tool_name"] for e in self.manager.history(aid)["activity"]}, {"read"})

    def test_daemon_failure_does_not_create_local_runtime(self):
        aid = self.spawn()
        record = self.manager._agents[aid]
        self.manager.execution_owner = "daemon"
        self.manager._persist_record_unlocked(record)
        client = types.SimpleNamespace(subagent_control=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline")))
        control = SubagentControl(self.storage, self.config, client)
        self.workers[0].content_signal.emit("durable partial")
        with patch("core.subagent_control.get_agent_manager_registry") as registry:
            page = control.request("parent", "history", agent_id=aid)
            self.assertTrue(page["summary"]["read_only"])
            self.assertIn("durable partial", [m.get("content") for m in page["messages"]])
            with self.assertRaises(ConnectionError):
                control.request("parent", "send", agent_id=aid, message_id="u2", content="hello")
            registry.assert_not_called()

    def test_real_daemon_protocol_routes_same_manager_and_validates_parent(self):
        import socketserver
        from core.daemon import DaemonRequestHandler, DaemonClient
        manager = get_agent_manager_registry().get_session_manager("parent", self.storage, self.config, worker_factory=self.factory)
        manager.execution_owner = "daemon"
        aid = manager.spawn_agent("daemon task")["agent_id"]
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), DaemonRequestHandler)
        server.state = types.SimpleNamespace(chat_storage=self.storage, config_manager=self.config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = DaemonClient(port=server.server_address[1])
            rows = client.subagent_control("parent", "list")
            self.assertEqual(rows[0]["id"], aid)
            client.subagent_control("parent", "send", agent_id=aid, message_id="u2", content="follow up")
            self.assertEqual(len(self.storage.agent_inbox(aid)), 2)
            with self.assertRaises(RuntimeError):
                client.subagent_control("other", "history", agent_id=aid)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()

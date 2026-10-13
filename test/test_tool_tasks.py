import copy
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from core.tool_tasks import ToolTaskManager, is_pending_tool_result, tool_interaction_wait


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def eventually(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true")
        time.sleep(.005)


class ToolTasksTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.records = {}
        self.releases = []
        self.managers = []

    def tearDown(self):
        for release in self.releases:
            release.set()
        for manager in self.managers:
            manager.close()
            for task in manager.tasks.values():
                task.done.wait(3)

    def manager(self, **kwargs):
        def persist(task_id, payload):
            self.records.setdefault(task_id, {}).update(copy.deepcopy(payload))
        manager = ToolTaskManager("session", "run", persist=persist, clock=self.clock, **kwargs)
        self.managers.append(manager)
        return manager

    def blocked(self, result="result"):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def execute():
            started.set()
            release.wait(3)
            return result
        return execute, started, release

    def test_fast_tool_returns_original_value_after_durable_save(self):
        manager = self.manager()
        task, can_wait = manager.submit("one", "read", {}, "call", lambda: {"value": 1}, read_only=True)
        self.assertEqual(manager.await_initial(task, can_wait), {"value": 1})
        self.assertEqual(self.records["one"]["status"], "succeeded")
        self.assertFalse(task.yielded)

    def test_threshold_dedup_and_completion_keep_original_execution(self):
        manager = self.manager()
        execute, started, release = self.blocked()
        task, _ = manager.submit("one", "read", {}, "call", execute, read_only=True)
        self.assertTrue(started.wait(1))
        answers = []
        waiter = threading.Thread(target=lambda: answers.append(manager.await_initial(task)))
        waiter.start()
        self.clock.advance(119)
        time.sleep(.04)
        self.assertEqual(answers, [])
        self.clock.advance(1)
        waiter.join(1)
        self.assertTrue(is_pending_tool_result(answers[0]))
        same, can_wait = manager.submit("two", "read", {}, "call2", lambda: self.fail("duplicate"), read_only=True)
        self.assertIs(same, task)
        self.assertFalse(can_wait)
        release.set()
        self.assertTrue(task.done.wait(1))
        self.assertEqual(manager.wait("one")["result"], "result")
        self.assertEqual(manager.ready(), [task])
        manager.mark_delivered("one", "message")
        self.assertEqual(manager.ready(), [])
        self.assertEqual(manager.wait("one")["status"], "already_delivered")
        self.assertEqual(self.records["one"]["delivery_message_id"], "message")

    def test_completed_at_threshold_returns_real_result(self):
        manager = self.manager()
        task, _ = manager.submit("one", "read", {}, "call", lambda: "done", read_only=True)
        self.assertTrue(task.done.wait(1))
        self.clock.advance(120)
        self.assertEqual(manager.await_initial(task), "done")
        self.assertFalse(task.yielded)

    def test_human_interaction_pauses_initial_clock(self):
        manager = self.manager()
        started, release = threading.Event(), threading.Event()
        executing, finish = threading.Event(), threading.Event()
        self.releases.extend([release, finish])
        @tool_interaction_wait
        def approval():
            started.set()
            release.wait(3)
        def execute():
            approval()
            executing.set()
            finish.wait(3)
            return "done"
        task, _ = manager.submit("one", "write", {}, "call", execute)
        self.assertTrue(started.wait(1))
        self.clock.advance(600)
        self.assertEqual(task.elapsed(), 0)
        release.set()
        self.assertTrue(executing.wait(1))
        self.clock.advance(120)
        self.assertTrue(is_pending_tool_result(manager.await_initial(task)))
        self.assertEqual(task.elapsed(), 120)

    def test_wait_can_yield_repeatedly_and_finish_early(self):
        manager = self.manager()
        execute, started, release = self.blocked()
        task, _ = manager.submit("one", "read", {}, "call", execute, read_only=True)
        self.assertTrue(started.wait(1))
        manager.pending_result(task)
        for _ in range(3):
            answers = []
            waiter = threading.Thread(target=lambda: answers.append(manager.wait("one")))
            waiter.start()
            time.sleep(.03)
            self.clock.advance(120)
            waiter.join(1)
            self.assertEqual(answers[0]["status"], "running")
        release.set()
        self.assertEqual(manager.wait("one")["result"], "result")
        self.assertEqual(manager.wait("foreign")["status"], "error")
        self.assertEqual(self.manager().wait("one")["status"], "error")

    def test_batch_order_and_cross_batch_writes_are_serial(self):
        manager = self.manager()
        execute, started, release = self.blocked()
        first, _ = manager.submit("one", "write", {"i": 1}, "call", execute)
        self.assertTrue(started.wait(1))
        manager.pending_result(first)
        second_started, third_started = threading.Event(), threading.Event()
        second, can_wait = manager.submit("two", "write", {"i": 2}, "call2", second_started.set, predecessor="one")
        self.assertFalse(can_wait)
        self.assertEqual(manager.await_initial(second, can_wait)["status"], "queued")
        third, _ = manager.submit("three", "write", {"i": 3}, "call3", third_started.set)
        read, _ = manager.submit("read", "read", {}, "call4", lambda: "independent", read_only=True)
        self.assertEqual(manager.await_initial(read), "independent")
        self.assertFalse(second_started.is_set())
        self.assertFalse(third_started.is_set())
        release.set()
        self.assertTrue(third.done.wait(1))
        self.assertTrue(second_started.is_set())
        self.assertTrue(third_started.is_set())

    def test_close_cancels_queue_and_archives_late_result(self):
        manager = self.manager()
        execute, started, release = self.blocked()
        first, _ = manager.submit("one", "write", {}, "call", execute)
        self.assertTrue(started.wait(1))
        manager.pending_result(first)
        second, _ = manager.submit("two", "write", {"i": 2}, "call2", lambda: self.fail("cancelled queue ran"))
        manager.close()
        self.assertEqual(self.records["one"]["status"], "unknown")
        self.assertTrue(second.done.wait(1))
        self.assertEqual(self.records["two"]["status"], "cancelled")
        release.set()
        self.assertTrue(first.done.wait(1))
        self.assertTrue(self.records["one"]["late_result"])
        self.assertEqual(self.records["one"]["result_text"], "result")

    def test_failed_result_save_is_not_delivered_as_success(self):
        def persist(task_id, payload):
            if payload.get("status") == "succeeded":
                raise PermissionError("disk failure")
        manager = ToolTaskManager("session", "run", persist=persist)
        self.managers.append(manager)
        task, _ = manager.submit("one", "read", {}, "call", lambda: "retained output", read_only=True)
        self.assertTrue(task.done.wait(1))
        self.assertEqual(task.result, "retained output")
        with self.assertRaises(PermissionError):
            manager.wait("one")


class ToolTasksWorkerTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows process-tree cancellation")
    def test_stop_terminates_real_command_descendants_and_retains_output(self):
        import ctypes
        import subprocess
        import sys
        from pathlib import Path
        from core.process_utils import subprocess_kwargs_no_window
        from test_command_tools import _load_command_tools_module

        commands = _load_command_tools_module()
        owner, handles, errors, processes = [], [], [], []
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory, "child.pid")
            code = ("import subprocess,sys,time,pathlib; "
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
                    "print('partial output before stop',flush=True); "
                    f"pathlib.Path({str(ready)!r}).write_text(str(child.pid)); time.sleep(20)")

            def launch(*args, **kwargs):
                process = subprocess.Popen([sys.executable, "-u", "-c", code],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, **subprocess_kwargs_no_window())
                processes.append(process)
                return process

            def stop_after_started():
                try:
                    eventually(lambda: ready.exists() and ready.stat().st_size > 0)
                    for pid in (processes[0].pid, int(ready.read_text())):
                        handle = kernel32.OpenProcess(0x00100000, False, pid)
                        if not handle:
                            raise AssertionError("Cannot observe test process")
                        handles.append(handle)
                    owner[0].stop()
                except Exception as exc:
                    errors.append(exc)
                    if owner:
                        owner[0].stop()

            stopper = threading.Thread(target=stop_after_started)
            def on_ready(worker):
                owner.append(worker)
                stopper.start()
            def streams(turn, messages):
                yield self.call()
            try:
                with patch.object(commands, "run_in_sandbox", side_effect=launch):
                    requests, final, _ = self.run_worker(streams,
                        lambda name, args, context: commands.bash(directory, "test command", context),
                        manager_wait=5, on_ready=on_ready)
                stopper.join(3)
                self.assertFalse(errors, errors)
                self.assertEqual(len(requests), 1)
                for handle in handles:
                    self.assertEqual(kernel32.WaitForSingleObject(handle, 1000), 0)
                task = next(iter(owner[0].tool_tasks.tasks.values()))
                self.assertEqual(task.result["status"], "cancelled")
                self.assertIn("partial output before stop", task.result["stdout"])
            finally:
                for handle in handles:
                    kernel32.CloseHandle(handle)
                for process in processes:
                    if process.poll() is None:
                        from core.process_utils import terminate_process_tree
                        terminate_process_tree(process)
                stopper.join(3)

    def run_worker(self, streams, execute, *, manager_wait=.02, on_ready=None, history=None, expect_error=False):
        from core.agent import LLMWorker
        from test_core import _DaemonConfigStub
        requests, finished, events = [], [], []

        class Skills:
            def get_tool_definitions(self, **kwargs):
                return [{"type": "function", "function": {"name": "slow", "description": "slow tool",
                        "parameters": {"type": "object", "properties": {}}}}]
            def get_system_prompts(self, **kwargs): return ""
            def get_skill_of_tool(self, name): return ""
            def get_tool_record(self, name): return {"source_kind": "core_builtin", "read_only": True}
            def call_tool(self, name, args, context=None): return execute(name, args, context)

        class Provider:
            provider_name, model_name, base_url = "stub", "stub", "https://provider.example/v1"
            api_protocol, thinking_enabled = "chat_completions", False
            requires_responses_replay = requires_deepseek_responses_replay = False
            def chat_stream(self, messages, tools=None, prompt_cache_key=None):
                requests.append(copy.deepcopy(messages))
                if len(requests) > 10:
                    raise AssertionError("model busy loop")
                yield from streams(len(requests), messages)

        with tempfile.TemporaryDirectory() as root:
            with patch("core.agent.SkillManager", return_value=Skills()), patch("core.agent.LLMFactory.create_provider", return_value=Provider()), patch("core.tool_tasks.TOOL_WAIT_SECONDS", manager_wait):
                worker = LLMWorker(history or [{"role": "user", "content": "work"}],
                    _DaemonConfigStub(root, values={"api_key": "test-key"}), workspace_dir=root,
                    session_id="session", turn_id="turn", request_id="run", run_context={"mode": "execution"})
                worker.finished_signal.connect(finished.append)
                worker.tool_result_signal.connect(events.append)
                if on_ready:
                    on_ready(worker)
                worker.run()
                for task in worker.tool_tasks.tasks.values():
                    task.done.wait(2)
                self.assertTrue(finished)
                if expect_error:
                    self.assertTrue(finished[-1].get("error"))
                else:
                    self.assertNotIn("error", finished[-1], finished[-1])
        return requests, finished[-1], events

    @staticmethod
    def call(name="slow", call_id="call", args=None, index=0):
        return {"type": "tool_call", "index": index, "id": call_id,
                "function": {"name": name, "arguments": json.dumps(args or {})}}

    def test_pending_final_reply_parks_then_delivers_result_once(self):
        release, started = threading.Event(), threading.Event()
        calls = []
        def execute(name, args, context):
            calls.append(name)
            started.set()
            release.wait(2)
            return {"content": "real result"}
        def streams(turn, messages):
            if turn == 1:
                yield self.call()
            elif turn == 2:
                self.assertTrue(is_pending_tool_result(next(m for m in messages if m.get("role") == "tool")["result_obj"]))
                threading.Timer(.05, release.set).start()
                yield {"type": "content", "content": "正在等结果。"}
            else:
                yield {"type": "content", "content": "已得到真实结果。"}
        requests, final, events = self.run_worker(streams, execute)
        self.assertEqual(calls, ["slow"])
        self.assertEqual(len(requests), 3)
        completions = [m for m in final["generated_messages"] if (m.get("meta") or {}).get("kind") == "tool_completion"]
        self.assertEqual(len(completions), 1)
        self.assertIn("real result", completions[0]["content"])
        self.assertEqual(len([m for m in final["generated_messages"] if m.get("tool_call_id") == "call"]), 1)
        self.assertEqual(requests[2][:len(requests[1])], requests[1])

    def test_wait_result_and_automatic_delivery_do_not_duplicate(self):
        release = threading.Event()
        def execute(name, args, context):
            release.wait(2)
            return "real result"
        def streams(turn, messages):
            if turn == 1:
                yield self.call()
            elif turn == 2:
                task_id = next(m["result_obj"]["task_id"] for m in messages if is_pending_tool_result(m.get("result_obj")))
                release.set()
                yield self.call("wait_tool", "wait", {"task_id": task_id})
            else:
                yield {"type": "content", "content": "完成。"}
        requests, final, events = self.run_worker(streams, execute)
        self.assertEqual(len(requests), 3)
        self.assertFalse(any((m.get("meta") or {}).get("kind") == "tool_completion" for m in final["generated_messages"]))
        result = next(m["result_obj"] for m in final["generated_messages"] if m.get("tool_call_id") == "wait")
        self.assertEqual(result["result"], "real result")

    def test_same_batch_returns_queued_and_keeps_protocol_closed(self):
        from core.conversation_integrity import ensure_tool_call_sequence
        release, second_ran = threading.Event(), threading.Event()
        calls = []
        def execute(name, args, context):
            calls.append(args["i"])
            if args["i"] == 1:
                release.wait(2)
            else:
                second_ran.set()
            return {"content": f"result-{args['i']}"}
        def streams(turn, messages):
            ensure_tool_call_sequence(messages, context="test pending batch")
            if turn == 1:
                yield self.call(args={"i": 1})
                yield self.call(call_id="second", args={"i": 2}, index=1)
            elif turn == 2:
                results = [m["result_obj"] for m in messages if m.get("role") == "tool"]
                self.assertEqual([r["status"] for r in results], ["running", "queued"])
                self.assertFalse(second_ran.is_set())
                release.set()
                self.assertTrue(second_ran.wait(1))
                yield {"type": "content", "content": "等待两项结果。"}
            else:
                yield {"type": "content", "content": "完成。"}
        requests, final, _ = self.run_worker(streams, execute)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(len([m for m in final["generated_messages"]
                              if (m.get("meta") or {}).get("kind") == "tool_completion"]), 2)

    def test_repeated_wait_is_not_a_tool_or_reasoning_loop(self):
        release = threading.Event()
        def execute(name, args, context):
            release.wait(3)
            return "done"
        def streams(turn, messages):
            if turn == 1:
                yield self.call()
            elif turn <= 6:
                task_id = next(m["result_obj"]["task_id"] for m in messages if is_pending_tool_result(m.get("result_obj")))
                if turn == 6:
                    release.set()
                yield {"type": "reasoning", "content": "原工具仍在执行，继续等待其结果。"}
                yield self.call("wait_tool", f"wait-{turn}", {"task_id": task_id})
            else:
                yield {"type": "content", "content": "完成。"}
        requests, final, _ = self.run_worker(streams, execute)
        self.assertEqual(len(requests), 7)
        self.assertEqual(final["content"], "完成。")

    def test_guidance_is_applied_while_parked_without_losing_pending_task(self):
        release = threading.Event()
        owner = []
        def execute(name, args, context):
            release.wait(3)
            return "done"
        def streams(turn, messages):
            if turn == 1:
                yield self.call()
            elif turn == 2:
                threading.Timer(.05, lambda: owner[0].steer({"id": "guidance", "role": "user", "content": "先补充说明"})).start()
                yield {"type": "content", "content": "等待。"}
            elif turn == 3:
                self.assertTrue(any(m.get("content") == "先补充说明" for m in messages))
                release.set()
                yield {"type": "content", "content": "已收到补充。"}
            else:
                yield {"type": "content", "content": "完成。"}
        requests, final, _ = self.run_worker(streams, execute, on_ready=owner.append)
        self.assertEqual(len(requests), 4)
        self.assertEqual(len([m for m in final["generated_messages"] if m.get("id") == "guidance"]), 1)

    def test_recovery_delivers_saved_result_without_replaying_tool(self):
        pending = {"background_tool": True, "status": "running", "task_id": "old-task"}
        history = [{"id": "u", "role": "user", "content": "previous"},
                   {"id": "a", "role": "assistant", "tool_calls": [{"id": "old-call", "type": "function", "function": {"name": "slow", "arguments": "{}"}}]},
                   {"id": "t", "role": "tool", "tool_call_id": "old-call", "content": json.dumps(pending), "result_obj": pending},
                   {"id": "new", "role": "user", "content": "recover"}]
        def prepare(worker):
            worker.runtime_journal.record_tool("session", "old-task", {
                "background_tool": True, "task_id": "old-task", "run_id": "old-run", "tool_call_id": "old-call",
                "name": "slow", "status": "succeeded", "result_obj": {"content": "saved result"}, "result_delivered": True})
        def streams(turn, messages):
            self.assertTrue(any("saved result" in (m.get("content") or "") for m in messages))
            yield {"type": "content", "content": "恢复完成。"}
        requests, final, _ = self.run_worker(streams, lambda *a: self.fail("replayed"), on_ready=prepare, history=history)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len([m for m in final["generated_messages"] if (m.get("meta") or {}).get("recovered")]), 1)

    def test_stop_during_park_keeps_output_and_does_not_resume_model(self):
        owner = []
        def execute(name, args, context):
            eventually(context["abort_check"])
            return {"status": "cancelled", "content": "partial output"}
        def streams(turn, messages):
            if turn == 1:
                yield self.call()
            else:
                threading.Timer(.04, owner[0].stop).start()
                yield {"type": "content", "content": "已保留阶段成果。"}
        requests, final, _ = self.run_worker(streams, execute, on_ready=owner.append)
        self.assertEqual(len(requests), 2)
        self.assertTrue(any(m.get("content") == "已保留阶段成果。" for m in final["generated_messages"]))
        task = next(iter(owner[0].tool_tasks.tasks.values()))
        self.assertIn("partial output", str(task.result))
        self.assertEqual(owner[0].tool_tasks.ready(), [])

    def test_background_save_failure_retains_output_and_stops_before_next_model_request(self):
        for wait_tool in (False, True):
            with self.subTest(wait_tool=wait_tool):
                release = threading.Event()
                def prepare(worker):
                    save = worker.runtime_journal.record_tool
                    def persist(session_id, execution_id, value, **kwargs):
                        if value.get("background_tool") and value.get("status") == "succeeded":
                            raise PermissionError("test disk failure")
                        return save(session_id, execution_id, value, **kwargs)
                    worker.runtime_journal.record_tool = persist
                def execute(name, args, context):
                    release.wait(2)
                    return "irreplaceable output"
                def streams(turn, messages):
                    if turn == 1:
                        yield self.call()
                    else:
                        release.set()
                        if wait_tool:
                            task_id = next(m["result_obj"]["task_id"] for m in messages if is_pending_tool_result(m.get("result_obj")))
                            yield self.call("wait_tool", "wait", {"task_id": task_id})
                        else:
                            yield {"type": "content", "content": "阶段回复。"}
                requests, final, _ = self.run_worker(streams, execute, on_ready=prepare, expect_error=True)
                self.assertEqual(len(requests), 2)
                self.assertIn("irreplaceable output", str(final["generated_messages"]))

    def test_recovery_marks_interrupted_execution_unknown_without_replaying(self):
        from core.agent import LLMWorker
        from core.runtime_journal import RuntimeJournal
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            journal = RuntimeJournal(root)
            journal.record_tool("session", "task", {"background_tool": True, "task_id": "task", "run_id": "old",
                                "name": "write", "tool_call_id": "call", "status": "started"})
            messages = [{"role": "tool", "tool_call_id": "call", "result_obj": {
                "background_tool": True, "status": "running", "task_id": "task"}}]
            generated = []
            owner = SimpleNamespace(runtime_journal=journal, session_id="session", request_id="new", turn_id="turn",
                                    _ensure_tool_tasks=lambda: SimpleNamespace(tasks={}),
                                    _append_ledger_message=lambda m, g, item: (m.append(item), g.append(item)),
                                    _checkpoint_generated_ledger=lambda *a, **k: None)
            LLMWorker._recover_tool_task_results(owner, messages, generated)
            self.assertEqual(journal.get_tool("session", "task")["status"], "unknown")
            self.assertEqual(generated[0]["result_obj"]["status"], "unknown")
            owner._tool_tasks_recovered = False
            LLMWorker._recover_tool_task_results(owner, messages, generated)
            self.assertEqual(len(generated), 1)


class ToolTasksProjectionTest(unittest.TestCase):
    def test_completion_updates_original_card_without_fake_user_or_ledger_mutation(self):
        from core.conversation_render import project_visible_messages
        pending = {"background_tool": True, "status": "running", "task_id": "task"}
        messages = [{"id": "u", "role": "user", "content": "work"},
                    {"id": "t", "role": "tool", "tool_call_id": "call", "content": json.dumps(pending), "result_obj": pending},
                    {"id": "c", "role": "user", "content": "runtime result", "result_obj": {"answer": 42},
                     "meta": {"kind": "tool_completion", "hidden": True, "task_id": "task", "tool_call_id": "call"}}]
        original = copy.deepcopy(messages)
        visible = project_visible_messages(messages)
        self.assertEqual([m["role"] for m in visible], ["user", "tool"])
        self.assertEqual(visible[1]["result_obj"], {"answer": 42})
        self.assertEqual(messages, original)
        # A recovery event can be appended after the next real user message.
        messages.insert(2, {"id": "next", "role": "user", "content": "recover"})
        page = project_visible_messages(messages, start=0, end=2)
        self.assertEqual(page[1]["result_obj"], {"answer": 42})
        visible = project_visible_messages(messages[:2], timeline_events=[{"tool_call_id": "call", "status": "interrupted"}])
        self.assertEqual(visible[1]["result_obj"]["status"], "unknown")

    def test_context_checkpoint_keeps_pending_task_identity(self):
        from types import SimpleNamespace
        from core.context_window import ContextWindowManager
        context = object.__new__(ContextWindowManager)
        context.session_id, context.run_id, context.current_user_id = "session", "run", "u"
        context.worker = SimpleNamespace(workspace_dir="workspace", tool_tasks=SimpleNamespace(
            snapshot=lambda: [{"task_id": "task", "status": "running", "result_saved": False}]))
        host = context._host_state([])
        self.assertEqual(host["pending_tool_tasks"][0]["task_id"], "task")

    def test_runtime_completion_is_at_provider_tail(self):
        from core.llm.providers import AnthropicProvider, OpenAIProvider
        completion = {"role": "user", "content": "[工具完成事件] result",
                      "meta": {"kind": "tool_completion", "hidden": True}}
        messages = [{"role": "system", "content": "stable"}, {"role": "user", "content": "work"}, completion]
        provider = object.__new__(OpenAIProvider)
        provider.supports_vision = False
        provider.model_name, provider.base_url = "model", "https://provider.example"
        projected = provider._prepare_messages(messages)
        self.assertEqual(projected[0]["content"], "stable")
        self.assertEqual(projected[-1]["content"], completion["content"])
        anthropic = object.__new__(AnthropicProvider)
        anthropic.supports_vision = False
        system, projected = anthropic._prepare_messages(messages)
        self.assertEqual(system.strip(), "stable")
        self.assertIn(completion["content"], str(projected[-1]["content"]))
        provider.requires_responses_replay = True
        provider.requires_deepseek_responses_replay = False
        response_input = provider._prepare_responses_input(messages)
        self.assertEqual(response_input[0]["content"][0]["text"], "stable")
        self.assertEqual(response_input[-1]["content"][0]["text"], completion["content"])


if __name__ == "__main__":
    unittest.main()

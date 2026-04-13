import importlib.util
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QCoreApplication, Signal

from core.agent_manager import AGENT_MANAGEMENT_TOOLS, SessionAgentManager
from core.chat_storage import ChatStorage


class _Signal:
    def __init__(self):
        self._handlers = []

    def connect(self, handler, *_args, **_kwargs):
        self._handlers.append(handler)

    def emit(self, payload):
        for handler in list(self._handlers):
            handler(payload)


class _FakeWorker:
    def __init__(self, messages, result_delay=0.05):
        self.messages = messages
        self.step_signal = _Signal()
        self.thinking_signal = _Signal()
        self.tool_call_signal = _Signal()
        self.finished_signal = _Signal()
        self._result_delay = result_delay
        self._running = False
        self._stopped = False

    def start(self):
        self._running = True

        def _run():
            time.sleep(self._result_delay)
            if self._stopped:
                payload = {"error": "stopped", "content": ""}
            else:
                last_user = ""
                for msg in reversed(self.messages):
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        last_user = msg.get("content") or ""
                        break
                payload = {
                    "content": f"done:{last_user}",
                    "generated_messages": [
                        {
                            "id": f"a-{int(time.time() * 1000)}",
                            "role": "assistant",
                            "content": f"done:{last_user}",
                        }
                    ],
                }
            self._running = False
            self.finished_signal.emit(payload)

        threading.Thread(target=_run, daemon=True).start()

    def stop(self):
        self._stopped = True

    def quit(self):
        return None

    def wait(self, _timeout=0):
        return True

    def terminate(self):
        self._stopped = True
        self._running = False

    def isRunning(self):
        return self._running


class _ConfigStub:
    def __init__(self, history_dir):
        self._history_dir = history_dir

    def get(self, _key, default=None):
        return default

    def get_chat_history_dir(self):
        return self._history_dir


class _Factory:
    def __init__(self):
        self.delay = 0.05

    def __call__(self, messages, *_args, **_kwargs):
        return _FakeWorker(messages, result_delay=self.delay)


class _AgentStateCapture:
    def __init__(self):
        self.items = []
        self.lock = threading.Lock()

    def emit(self, payload):
        with self.lock:
            self.items.append(payload)


class _QtEmitter(QObject):
    finished_signal = Signal(dict)
    step_signal = Signal(str)
    thinking_signal = Signal(str)
    content_signal = Signal(str)
    output_signal = Signal(str)
    tool_call_signal = Signal(dict)


class _QtWorker:
    def __init__(self, messages):
        self.messages = messages
        self._running = False
        self._thread = None
        self._emitter = _QtEmitter()
        self.finished_signal = self._emitter.finished_signal
        self.step_signal = self._emitter.step_signal
        self.thinking_signal = self._emitter.thinking_signal
        self.content_signal = self._emitter.content_signal
        self.output_signal = self._emitter.output_signal
        self.tool_call_signal = self._emitter.tool_call_signal

    def start(self):
        self._running = True

        def _run():
            self.step_signal.emit("Turn 1: Requesting LLM...")
            self.output_signal.emit("Provider Start: test-provider")
            self.content_signal.emit("partial-output")
            self.finished_signal.emit(
                {
                    "content": "done",
                    "generated_messages": [
                        {
                            "id": f"qt-{int(time.time() * 1000)}",
                            "role": "assistant",
                            "content": "done",
                        }
                    ],
                }
            )
            self._running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        return None

    def quit(self):
        return None

    def wait(self, _timeout=0):
        if self._thread:
            self._thread.join(timeout=max((_timeout or 0) / 1000.0, 0.01))
        return not self._running

    def terminate(self):
        self._running = False

    def isRunning(self):
        return self._running


class TestAgentManagerTools(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "chat_history.sqlite")
        self.storage = ChatStorage(self.db_path)
        self.conversation_id = "conv-main"
        self.storage.upsert_conversation(self.conversation_id, title="demo", status="active", meta={})
        self.config = _ConfigStub(self.temp_dir)
        self.factory = _Factory()
        self.manager = SessionAgentManager(
            self.conversation_id,
            chat_storage=self.storage,
            config_manager=self.config,
            workspace_dir=self.temp_dir,
            worker_factory=self.factory,
            max_live_agents=8,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_spawn_agent_persists_record_and_transcript(self):
        result = self.manager.spawn_agent(message="task A", name="worker-a", fork_context=False)
        agent = self.storage.get_agent(result["agent_id"])
        self.assertIsNotNone(agent)
        self.assertEqual(agent["name"], "worker-a")
        messages = self.storage.get_agent_messages(result["agent_id"])
        self.assertGreaterEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "task A")

    def test_fork_context_initializes_agent_messages_from_snapshot(self):
        self.factory.delay = 0.2
        snapshot = [
            {"id": "u1", "role": "user", "content": "ctx-user"},
            {"id": "a1", "role": "assistant", "content": "ctx-assistant"},
        ]
        result = self.manager.spawn_agent(
            message="fork-task",
            name="forked",
            fork_context=True,
            current_messages_snapshot=snapshot,
        )
        messages = self.storage.get_agent_messages(result["agent_id"])
        self.assertGreaterEqual(len(messages), 3)
        self.assertEqual(messages[0]["content"], "ctx-user")
        self.assertEqual(messages[1]["content"], "ctx-assistant")
        self.assertEqual(messages[2]["content"], "fork-task")

    def test_send_input_continues_existing_agent(self):
        spawned = self.manager.spawn_agent(message="first task")
        self.manager.wait_agent([spawned["agent_id"]], timeout_ms=3000, return_when="all")
        queued = self.manager.send_input(spawned["agent_id"], "second task")
        self.assertEqual(queued["status"], "queued")
        self.manager.wait_agent([spawned["agent_id"]], timeout_ms=3000, return_when="all")
        messages = self.storage.get_agent_messages(spawned["agent_id"])
        self.assertTrue(any(msg.get("content") == "second task" for msg in messages))

    def test_wait_agent_timeout_and_pending(self):
        self.factory.delay = 0.3
        spawned = self.manager.spawn_agent(message="slow task")
        waited = self.manager.wait_agent([spawned["agent_id"]], timeout_ms=10, return_when="all")
        self.assertTrue(waited["timed_out"])
        self.assertEqual(len(waited["pending"]), 1)

    def test_close_agent_blocks_future_input(self):
        self.factory.delay = 0.4
        spawned = self.manager.spawn_agent(message="close me")
        close_reason = "user requested stop"
        closed = self.manager.close_agent(spawned["agent_id"], force=True, reason=close_reason)
        self.assertIn(closed["status"], {"closed", "killed"})
        stored = self.storage.get_agent(spawned["agent_id"])
        self.assertEqual(stored["last_error"], close_reason)
        with self.assertRaises(ValueError):
            self.manager.send_input(spawned["agent_id"], "after close")

    def test_list_agents_supports_status_filter(self):
        one = self.manager.spawn_agent(message="alpha", name="agent-alpha")
        two = self.manager.spawn_agent(message="beta", name="agent-beta")
        self.manager.wait_agent([one["agent_id"], two["agent_id"]], timeout_ms=4000, return_when="all")
        all_items = self.manager.list_agent_summaries()
        self.assertGreaterEqual(len(all_items), 2)
        completed = self.manager.list_agent_summaries(status_filter="completed")
        self.assertTrue(all(item["status"] == "completed" for item in completed))

    def test_subagent_management_tools_are_filtered_from_llmworker(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self):
                defs = []
                for name in sorted(AGENT_MANAGEMENT_TOOLS | {"bash"}):
                    defs.append({"type": "function", "function": {"name": name, "description": "", "parameters": {}}})
                return defs

            def check_for_updates(self):
                return False

        from core.agent import LLMWorker

        with patch("core.agent.SkillManager", _SkillManagerStub):
            worker = LLMWorker(
                [{"role": "user", "content": "hi"}],
                self.config,
                workspace_dir=self.temp_dir,
                parent_agent_id="sub-1",
                session_id=self.conversation_id,
                is_subagent=True,
            )
        tool_names = {item["function"]["name"] for item in worker.tools}
        self.assertIn("bash", tool_names)
        self.assertFalse(tool_names.intersection(AGENT_MANAGEMENT_TOOLS))

    def test_skill_exports_and_subagent_denial(self):
        module_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills",
            "agent-manager",
            "impl.py",
        )
        spec = importlib.util.spec_from_file_location("agent_manager_skill_impl", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        export_names = {item["name"] for item in module.TOOL_EXPORTS}
        self.assertSetEqual(
            export_names,
            {"spawn_agent", "send_input", "wait_agent", "close_agent", "list_agents"},
        )
        with self.assertRaises(PermissionError):
            module.spawn_agent("x", _context={"is_subagent": True})

    def test_qt_worker_logs_and_completion_are_relayed(self):
        capture = _AgentStateCapture()

        def _factory(messages, *_args, **_kwargs):
            return _QtWorker(messages)

        manager = SessionAgentManager(
            self.conversation_id,
            chat_storage=self.storage,
            config_manager=self.config,
            workspace_dir=self.temp_dir,
            worker_factory=_factory,
            agent_state_signal=capture,
            max_live_agents=8,
        )

        spawned = manager.spawn_agent(message="qt task", name="qt-agent")
        waited = manager.wait_agent([spawned["agent_id"]], timeout_ms=3000, return_when="all")
        self.assertFalse(waited["timed_out"])

        statuses = [item.get("status") for item in capture.items]
        self.assertIn("log", statuses)
        self.assertIn("content", statuses)
        self.assertIn("completed", statuses)


if __name__ == "__main__":
    unittest.main()

"""New calls observe current state; recovery reuses only the same execution."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from core.agent import LLMWorker
from core.runtime_journal import RuntimeJournal
from test_core import _DaemonConfigStub


class Skills:
    def __init__(self, execute):
        self.execute = execute
        self.calls = []

    def get_tool_definitions(self, *args, **kwargs):
        return []

    def check_for_updates(self):
        return False

    def get_system_prompts(self, *args, **kwargs):
        return ""

    def get_brief_skill_prompt(self, *args):
        return ""

    def get_skill_display_name(self, name):
        return name

    def get_skill_of_tool(self, name):
        return ""

    def get_tool_record(self, name):
        return {"source_kind": "core_builtin", "read_only": name == "read_file",
                "destructive": name != "read_file"}

    def call_tool(self, name, args, context=None):
        self.calls.append((name, copy.deepcopy(args)))
        return self.execute(name, args, context)


class Provider:
    provider_name = "stub"
    model_name = "stub"
    base_url = ""
    thinking_enabled = False

    def __init__(self, operations):
        self.operations = operations
        self.requests = []

    def chat_stream(self, messages, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        index = len(self.requests) - 1
        if index < len(self.operations):
            operation = self.operations[index]
            name, args = operation(messages) if callable(operation) else operation
            yield {"type": "tool_call", "index": 0, "id": f"call-{index}",
                   "function": {"name": name, "arguments": json.dumps(args)}}
        else:
            yield {"type": "content", "content": "verified"}


class ToolResultFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())

    def run_worker(self, operations, execute, *, session="freshness", run="run-1"):
        skills, provider = Skills(execute), Provider(operations)
        finished, events = [], []
        with patch("core.agent.SkillManager", return_value=skills), \
                patch("core.agent.LLMFactory.create_provider", return_value=provider):
            worker = LLMWorker(
                [{"role": "user", "content": "Inspect current state after each operation."}],
                _DaemonConfigStub(self.root, {"api_key": "test-key"}),
                workspace_dir=self.root, session_id=session, request_id=run, turn_id=run,
                run_context={"mode": "execution"},
            )
            worker.finished_signal.connect(finished.append)
            worker.observability_signal.connect(events.append)
            worker.run()
        self.assertTrue(finished)
        self.assertNotIn("error", finished[0])
        self.assertEqual(finished[0]["content"], "verified")
        return skills, provider, events, worker

    def test_browser_snapshot_and_click_execute_again_after_page_changes(self):
        path = Path(__file__).resolve().parents[1] / "ai_skills/browser-automation/impl.py"
        spec = importlib.util.spec_from_file_location("browser_freshness_test", path)
        browser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(browser)
        state = {"page": "overview", "observed": False, "revision": 0}
        observations = []

        def cli(args, **kwargs):
            command = args[0]
            if command == "snapshot":
                state["observed"] = True
                observations.append(state["page"])
                return {"status": "completed", "result": {"text": state["page"], "revision": state["revision"]}}
            if command == "click":
                if not state["observed"]:
                    return {"status": "incomplete", "error": {"code": "ref_not_found"}}
                state.update(page=args[2], observed=False, revision=state["revision"] + 1)
            return {"status": "completed", "result": {"ok": True}}

        commands = [
            ["session", "start"], ["snapshot"], ["click", "--ref", "metrics"],
            ["snapshot"], ["click", "--ref", "about"], ["snapshot"],
            ["click", "--ref", "metrics"], ["snapshot"], ["session", "stop", "abcd"],
        ]
        operations = [("browser_skill_cli", {"args": args + (["--session", "abcd"]
                       if args[0] != "session" else []), "timeout_seconds": 60}) for args in commands]
        with patch.object(browser, "run_browser_skill_cli", side_effect=cli), \
                patch.object(browser, "log_browser_skill_event"):
            skills, provider, events, _ = self.run_worker(
                operations, lambda name, args, context: browser.browser_skill_cli(
                    **args, workspace_dir=self.root, _context=context))
        self.assertEqual(skills.calls, operations)
        self.assertEqual(observations, ["overview", "metrics", "about", "metrics"])
        self.assertFalse(any(e.get("type") == "tool_result_reused" for e in events))
        last_tools = [m["result_obj"] for m in provider.requests[-1] if m.get("role") == "tool"]
        self.assertEqual(last_tools[-2]["result"]["revision"], 3)

    def test_generic_read_after_write_and_repeated_action_are_not_cached(self):
        path = Path(self.root) / "state.txt"
        path.write_text("0")
        args = {"path": str(path)}
        def execute(name, args, context):
            current = int(path.read_text())
            if name == "write_file":
                path.write_text(str(current + 1))
            return {"status": "completed", "result": path.read_text()}
        operations = [(name, args) for name in ["read_file", "write_file", "read_file", "write_file", "read_file"]]
        skills, provider, _, _ = self.run_worker(operations, execute)
        self.assertEqual(skills.calls, operations)
        results = [m["result_obj"]["result"] for m in provider.requests[-1] if m.get("role") == "tool"]
        self.assertEqual(results, ["0", "1", "1", "2", "2"])

    def test_new_run_does_not_reuse_an_uncommitted_success(self):
        journal = RuntimeJournal(self.root)
        journal.record_tool("freshness", "previous-execution", {
            "run_id": "previous-run", "name": "read_file", "args_hash": RuntimeJournal.checksum({}),
            "status": "succeeded", "committed": False, "result_obj": {"content": "stale"},
        })
        skills, provider, _, _ = self.run_worker(
            [("read_file", {})], lambda *args: {"status": "completed", "content": "current"})
        self.assertEqual(len(skills.calls), 1)
        result = next(m["result_obj"] for m in provider.requests[-1] if m.get("role") == "tool")
        self.assertEqual(result["content"], "current")

    def test_other_conversation_success_is_never_reused(self):
        self.run_worker([("read_file", {})], lambda *args: {"content": "session A"}, session="a")
        skills, provider, _, _ = self.run_worker(
            [("read_file", {})], lambda *args: {"content": "session B"}, session="b")
        self.assertEqual(len(skills.calls), 1)
        result = next(m["result_obj"] for m in provider.requests[-1] if m.get("role") == "tool")
        self.assertEqual(result["content"], "session B")

    @unittest.skipUnless(os.environ.get("COWORK_BROWSER_LIVE_TEST") == "1",
                         "opt-in: needs an already connected BrowserSkill extension")
    def test_live_browser_observes_each_change_through_the_actual_agent_loop(self):
        """Only a newly created Agent Window visits our local fixture; always stop it."""
        from core import browser_skill_component
        path = Path(__file__).resolve().parents[1] / "ai_skills/browser-automation/impl.py"
        spec = importlib.util.spec_from_file_location("browser_live_freshness_test", path)
        browser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(browser)
        html = b'''<!doctype html><html><head><title>Browser freshness test</title></head>
        <body><button onclick="document.getElementById('view').textContent=['overview','metrics','about'][++window.step%3]">Next page</button>
        <h1 id="view">overview</h1><script>window.step=0</script></body></html>'''
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state = {}
        def last_result(messages):
            return next(m["result_obj"] for m in reversed(messages) if m.get("role") == "tool")

        def command(args):
            return "browser_skill_cli", {"args": args, "timeout_seconds": 60}

        def navigate(messages):
            state["session"] = last_result(messages)["result"]["session_id"]
            return command(["navigate", f"http://127.0.0.1:{server.server_port}/", "--session", state["session"]])

        def snapshot(messages):
            if "tab" not in state:
                state["tab"] = str(last_result(messages)["result"]["tab_id"])
            return command(["snapshot", "--session", state["session"], "--tab-id", state["tab"], "--max-tokens", "500"])

        def click(messages):
            text = last_result(messages)["result"]["text"]
            match = re.search(r'(@e\d+) button "Next page"', text)
            self.assertIsNotNone(match, text)
            return command(["click", "--ref", match[1], "--session", state["session"], "--tab-id", state["tab"]])

        operations = [command(["session", "start"]), navigate, snapshot, click, snapshot, click, snapshot,
                      lambda _: command(["observe", "--session", state["session"], "--tab-id", state["tab"], "--max-tokens", "500"]),
                      lambda _: command(["session", "stop", state["session"]])]
        def execute(name, args, context):
            result = browser.browser_skill_cli(**args, workspace_dir=self.root, _context=context)
            if result.get("status") == "completed":
                if args["args"][:2] == ["session", "start"]:
                    state["session"] = result["result"]["session_id"]
                elif args["args"][:2] == ["session", "stop"]:
                    state["stopped"] = True
            return result

        try:
            skills, provider, events, _ = self.run_worker(operations, execute)
            results = [m["result_obj"] for m in provider.requests[-1] if m.get("role") == "tool"]
            self.assertTrue(all(r.get("status") == "completed" for r in results), results)
            snapshots = [results[i]["result"]["text"] for i in [2, 4, 6]]
            for text, expected in zip(snapshots, ["overview", "metrics", "about"]):
                self.assertIn(f'heading "{expected}"', text)
            self.assertIn('heading "about"', results[7]["result"]["text"])
            self.assertEqual(len(skills.calls), 9)
            self.assertEqual(skills.calls[2], skills.calls[4])
            self.assertEqual(skills.calls[4], skills.calls[6])
            self.assertEqual(skills.calls[3], skills.calls[5])
            self.assertFalse(any(e.get("type") == "tool_result_reused" for e in events))
        finally:
            if state.get("session") and not state.get("stopped"):
                browser_skill_component.run_browser_skill_cli(["session", "stop", state["session"]])
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

import copy
import tempfile
import unittest
from pathlib import Path

from core.chat_storage import ChatStorage
from core.conversation_render import _is_hidden_context_message
from core.workbench_context import context_message
import test_agent_bootstrap as bootstrap


def submission(message_id="u1", *, tabs=None, session="session-1"):
    tabs = tabs if tabs is not None else [{"id": "tab-1", "kind": "web", "url": "https://example.test/live",
                                          "title": "Live page", "status": "ready", "owner": "available"}]
    active = tabs[0]["id"] if tabs else ""
    return {"id": message_id, "role": "user", "content": "帮我分析右侧打开的内容",
            "meta": {"workbench_context": {"session_id": session, "tabs": tabs,
                     "active_tab_id": active, "visible_tab_id": active}}}


class WorkbenchContextTests(unittest.TestCase):
    run_worker = bootstrap.TestAgentBootstrap.run_worker

    def test_submission_reaches_actual_model_request_without_changing_prefix_or_tools(self):
        source = submission()
        before = copy.deepcopy(source)
        profile = dict(bootstrap.PROFILE, bootstrap_plugin="")
        plain, *_ = self.run_worker([[{"type": "content", "content": "done"}]], profile=profile,
                                   history=[{key: value for key, value in source.items() if key != "meta"}])
        requests, result, *_ = self.run_worker([[{"type": "content", "content": "done"}]],
                                              profile=profile, history=[source])
        self.assertEqual(source, before)
        self.assertEqual(plain[0]["messages"][0], requests[0]["messages"][0])
        self.assertEqual(plain[0]["tools"], requests[0]["tools"])
        contexts = [m for m in requests[0]["messages"] if "https://example.test/live" in m.get("content", "")]
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["role"], "user")
        saved = next(m for m in result["generated_messages"] if (m.get("meta") or {}).get("source") == "workbench_submission")
        self.assertTrue(_is_hidden_context_message(saved))
        self.assertIn("不代表用户要求", saved["content"])

    def test_guidance_appends_its_own_snapshot_without_rewriting_prior_request(self):
        workers = []
        guidance = submission("u2", tabs=[{"id": "terminal", "kind": "terminal", "cwd": "D:/workspace", "status": "running"}])
        def first_response():
            workers[0].steer(guidance, expected_turn_id="turn-1")
            yield {"type": "content", "content": "第一段分析"}
        requests, result, *_ = self.run_worker(
            [first_response, [{"type": "content", "content": "终端分析"}]],
            profile=dict(bootstrap.PROFILE, bootstrap_plugin=""), history=[submission()],
            on_worker_ready=workers.append,
        )
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["messages"], requests[1]["messages"][:len(requests[0]["messages"])])
        self.assertEqual(requests[0]["tools"], requests[1]["tools"])
        self.assertIn("D:/workspace", str(requests[1]["messages"]))
        self.assertNotIn("D:/workspace", str(requests[0]["messages"]))
        saved = [m for m in result["generated_messages"] if (m.get("meta") or {}).get("source") == "workbench_submission"]
        self.assertEqual([m["meta"]["source_user_message_id"] for m in saved], ["u1", "u2"])

    def test_sqlite_reload_keeps_snapshot_and_retry_does_not_duplicate_context(self):
        source = submission()
        context = context_message(source["meta"]["workbench_context"], "session-1", "u1")
        with tempfile.TemporaryDirectory() as directory:
            storage = ChatStorage(str(Path(directory) / "history.sqlite"))
            storage.save_conversation("session-1", [source, context], title="Workbench")
            loaded = storage.get_messages("session-1")
        self.assertEqual(loaded[0]["meta"]["workbench_context"], source["meta"]["workbench_context"])
        self.assertIsNone(context_message(source["meta"]["workbench_context"], "session-1", "u1", loaded))
        requests, result, *_ = self.run_worker([[{"type": "content", "content": "done"}]],
            profile=dict(bootstrap.PROFILE, bootstrap_plugin=""), history=loaded)
        self.assertEqual(sum("https://example.test/live" in m.get("content", "") for m in requests[0]["messages"]), 1)
        self.assertFalse(any((m.get("meta") or {}).get("source") == "workbench_submission" for m in result["generated_messages"]))

    def test_closed_tabs_clear_old_reference_and_wrong_conversation_is_ignored(self):
        source = submission()
        context = context_message(source["meta"]["workbench_context"], "session-1", "u1")
        closed = submission("u2", tabs=[])
        new = context_message(closed["meta"]["workbench_context"], "session-1", "u2", [context])
        self.assertIn('"tabs": []', new["content"])
        self.assertIsNone(context_message(source["meta"]["workbench_context"], "another-session", "u1"))
        self.assertIsNone(context_message(closed["meta"]["workbench_context"], "session-1", "u2"))

    def test_followup_without_desktop_snapshot_does_not_reappend_old_state(self):
        source = submission()
        history = [source, {"id": "u2", "role": "user", "content": "来自其他入口的新请求"}]
        requests, result, *_ = self.run_worker([[{"type": "content", "content": "done"}]],
            profile=dict(bootstrap.PROFILE, bootstrap_plugin=""), history=history)
        self.assertFalse(any("https://example.test/live" in m.get("content", "") for m in requests[0]["messages"]))
        self.assertFalse(any((m.get("meta") or {}).get("source") == "workbench_submission" for m in result["generated_messages"]))

    def test_capture_error_is_unknown_not_an_empty_workbench(self):
        result = context_message({"session_id": "session-1", "error": "capture failed"}, "session-1", "u1")
        self.assertIn("capture failed", result["content"])
        self.assertNotIn('"tabs": []', result["content"])

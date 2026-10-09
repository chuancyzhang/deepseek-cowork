import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from core.request_snapshot import RequestSnapshotStore, capture_provider_request
from core.task_details import redact_diagnostic, parsed_result, tool_result_status, tool_result_summary
from core.llm.providers import OpenAIProvider, AnthropicProvider


class RequestSnapshotTests(unittest.TestCase):
    def test_latest_request_is_session_scoped_and_not_a_live_reference(self):
        with tempfile.TemporaryDirectory() as root:
            store = RequestSnapshotStore(root)
            original = {"model": "m", "messages": [{"role": "system", "content": "prefix"}],
                        "extra_headers": {"Authorization": "secret"}}
            before = copy.deepcopy(original)
            store.capture("A", "run1", "request1", "chat_completions", original)
            self.assertEqual(original, before)
            original["messages"][0]["content"] = "later"
            store.capture("B", "runB", "requestB", "responses", {"model": "b", "input": []})
            snapshot = store.read("A")
            self.assertEqual(snapshot["parameters"]["messages"][0]["content"], "prefix")
            self.assertNotIn("extra_headers", snapshot["parameters"])
            store.capture("A", "run2", "request2", "responses", {"model": "a2", "input": []})
            self.assertEqual(store.read("A")["request_id"], "request2")
            self.assertEqual(store.read("B")["request_id"], "requestB")
            store.delete("A")
            self.assertFalse(store.available("A"))
            self.assertTrue(store.available("B"))

    def test_capture_failure_blocks_old_snapshot_and_next_success_recovers(self):
        with tempfile.TemporaryDirectory() as root:
            store = RequestSnapshotStore(root)
            store.capture("A", "r", "1", "responses", {"model": "m"})
            with self.assertRaises(TypeError):
                store.capture("A", "r", "2", "responses", {"model": "m", "bad": object()})
            with self.assertRaises(ValueError):
                store.read("A")
            store.capture("A", "r", "3", "responses", {"model": "m"})
            self.assertEqual(store.read("A")["request_id"], "3")

    def test_optional_hook_failure_does_not_escape(self):
        capture_provider_request({}, "responses", {"capture_request": MagicMock(side_effect=OSError("disk"))})
        capture_provider_request({}, "responses", {})

    def test_worker_reports_capture_failure_without_body_and_excludes_subagents(self):
        from core.agent import LLMWorker
        with tempfile.TemporaryDirectory() as root:
            events = MagicMock()
            worker = SimpleNamespace(is_subagent=False, session_id="A", request_id="run",
                observability_signal=events, config_manager=SimpleNamespace(get_chat_history_dir=lambda: root))
            LLMWorker._capture_request_snapshot(worker, "responses", {"model": "m", "input": "private"}, "req")
            event = events.emit.call_args.args[0]
            self.assertEqual(event["status"], "completed")
            self.assertNotIn("private", json.dumps(event))
            with patch.object(RequestSnapshotStore, "capture", side_effect=OSError("disk")):
                LLMWorker._capture_request_snapshot(worker, "responses", {}, "req2")
            self.assertEqual(events.emit.call_args.args[0]["status"], "failed")
            worker.is_subagent = True
            events.reset_mock()
            LLMWorker._capture_request_snapshot(worker, "responses", {}, "child")
            events.emit.assert_not_called()

    def test_real_sdk_400_401_capture_final_parameters_before_network(self):
        from openai import OpenAI
        from anthropic import Anthropic
        for protocol in ("chat_completions", "responses", "anthropic"):
            for status in (400, 401):
                with self.subTest(protocol=protocol, status=status), tempfile.TemporaryDirectory() as root:
                    store = RequestSnapshotStore(root)
                    captured, sent = [], []

                    def capture(proto, params):
                        captured.append(copy.deepcopy(params))
                        store.capture("A", "run", "req", proto, params)

                    def handle(request):
                        self.assertTrue(store.available("A"))
                        sent.append(json.loads(request.content))
                        return httpx.Response(status, json={"error": {"message": "invalid model", "type": "invalid_request_error", "code": "invalid_model"}})

                    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
                        if protocol == "anthropic":
                            provider = AnthropicProvider("test-key", "https://mock.invalid", "model")
                            provider.client.close()
                            provider.client = Anthropic(api_key="test-key", base_url="https://mock.invalid", max_retries=0, http_client=http_client)
                        else:
                            provider = OpenAIProvider("test-key", "https://mock.invalid/v1", "model", api_protocol=protocol)
                            provider.client.close()
                            provider.client = OpenAI(api_key="test-key", base_url="https://mock.invalid/v1", max_retries=0, http_client=http_client)
                        messages = [{"role": "system", "content": "stable prefix"}, {"role": "user", "content": "hi"}]
                        before = copy.deepcopy(messages)
                        events = list(provider.chat_stream(messages, request_context={"capture_request": capture, "client_request_id": "req"}))
                        self.assertEqual(messages, before)
                        self.assertTrue(any(event["type"] == "error" for event in events))
                        self.assertEqual(len(captured), 1)
                        self.assertEqual(len(sent), 1)
                        params = store.read("A")["parameters"]
                        body = dict(params)
                        body.update(body.pop("extra_body", {}))
                        if protocol == "anthropic":
                            body["stream"] = True  # Added by the SDK's messages.stream wrapper.
                        self.assertEqual(sent[0], body)
                        provider.client.close()

    def test_capture_follows_security_projection_without_mutating_sdk_arguments(self):
        provider = OpenAIProvider("key", "https://mock.invalid/v1", "model")
        captured = []
        projected = {"model": "m", "messages": [{"role": "user", "content": "TOKEN_1"}], "stream": True}
        with patch("core.llm.providers.project_provider_request", return_value=projected), \
             patch.object(provider, "_create_chat_completion_stream", side_effect=ValueError("400")) as send:
            list(provider.chat_stream([{"role": "user", "content": "private"}], request_context={"capture_request": lambda protocol, params: captured.append(copy.deepcopy(params))}))
        self.assertEqual(captured, [projected])
        self.assertIs(send.call_args.args[0], projected)
        provider.client.close()

    def test_raw_details_preserve_full_content_and_non_json_text(self):
        payload = {"content": "完整内容" * 10000, "api_key": "private"}
        safe = redact_diagnostic(payload)
        self.assertEqual(safe["content"], payload["content"])
        self.assertEqual(safe["api_key"], "[已隐藏]")
        self.assertEqual(payload["api_key"], "private")
        self.assertEqual(parsed_result("plain\nreturned text"), "plain\nreturned text")
        self.assertEqual(parsed_result('{"ok":false}'), {"ok": False})

    def test_result_status_and_summary_are_not_transport_completion(self):
        value = '{"ok": false, "error": "domain required"}'
        self.assertEqual(tool_result_status(value), "error")
        self.assertEqual(tool_result_summary(value), "domain required")
        self.assertEqual(tool_result_status({"status": "partial_apply"}), "partial")
        self.assertEqual(tool_result_status({"ok": True}), "done")
        self.assertNotIn("{", tool_result_summary({"ok": True, "data": [1, 2]}))


if __name__ == "__main__":
    unittest.main()

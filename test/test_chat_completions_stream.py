import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from core.llm.chat_completions_stream import iter_chat_completions_events
from core.llm.providers import OpenAIProvider, ProviderStreamError


def frame(delta=None, finish=None, **extra):
    return {"choices": [{"delta": delta, "finish_reason": finish}], **extra}


def tool(index=0, call_id=None, name=None, arguments=None):
    return {"index": index, "id": call_id, "function": {"name": name, "arguments": arguments}}


def objects(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: objects(item) for key, item in value.items()})
    if isinstance(value, list):
        return [objects(item) for item in value]
    return value


class ChatCompletionsStreamTests(unittest.TestCase):
    def parse(self, chunks):
        return iter_chat_completions_events(
            chunks,
            usage_payload=lambda chunk: OpenAIProvider._usage_payload(None, chunk),
            error_factory=ProviderStreamError,
        )

    def test_sdk_and_dict_frames_have_identical_existing_events(self):
        chunks = [
            {"choices": []}, frame({"role": "assistant"}), frame(),
            frame({"reasoning_content": "分析", "content": "<think>原文</think>"}),
            frame({"tool_calls": [tool(1, "b", "second"), tool(0, "a", "first", '{"x":')]}),
            frame({"tool_calls": [tool(0, arguments='1}'), tool(1, arguments='{}')]}),
            frame(finish="tool_calls"),
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}},
        ]
        expected = [
            {"type": "reasoning", "content": "分析"},
            {"type": "content", "content": "<think>原文</think>"},
            {"type": "tool_call", "index": 1, "id": "b", "function": {"name": "second"}},
            {"type": "tool_call", "index": 0, "id": "a", "function": {"name": "first", "arguments": '{"x":'}},
            {"type": "tool_call", "index": 0, "function": {"arguments": '1}'}},
            {"type": "tool_call", "index": 1, "function": {"arguments": '{}'}},
            {"type": "provider_terminal", "status": "completed", "finish_reason": "tool_calls"},
            {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13, "cache_metrics_status": "unavailable"}},
        ]
        original = copy.deepcopy(chunks)
        for convert in (lambda value: value, objects):
            with self.subTest(convert=convert):
                self.assertEqual(list(self.parse(convert(chunks))), expected)
        self.assertEqual(chunks, original)

    def test_reasoning_alias_only_when_primary_is_absent_or_null(self):
        for primary, alias, expected in [
            (None, "alias", "alias"), ("primary", "alias", "primary"),
            ("", "alias", None), (None, {"text": "opaque"}, None),
        ]:
            with self.subTest(primary=primary, alias=alias):
                result = list(self.parse([frame({"reasoning_content": primary, "reasoning": alias}, "stop")]))
                reasoning = [event["content"] for event in result if event["type"] == "reasoning"]
                self.assertEqual(reasoning, [expected] if expected else [])
        result = list(self.parse([frame({"reasoning": "absent primary"}, "stop")]))
        self.assertEqual(result[0], {"type": "reasoning", "content": "absent primary"})

    def test_terminal_only_chunk_needs_no_delta(self):
        result = list(self.parse([{"choices": [{"finish_reason": "stop"}]}]))
        self.assertEqual(result, [{"type": "provider_terminal", "status": "completed", "finish_reason": "stop"}])

    def test_finish_reasons_and_eof_keep_existing_semantics(self):
        for reason in ("stop", "tool_calls", "length", "content_filter", "unknown"):
            with self.subTest(reason=reason):
                terminal = list(self.parse([frame(finish=reason)]))[0]
                self.assertEqual(terminal["status"], "completed" if reason in ("stop", "tool_calls") else "incomplete")
        with self.assertRaisesRegex(ProviderStreamError, "without a finish_reason"):
            list(self.parse([frame({"role": "assistant"})]))

    def test_in_band_error_status_is_preserved_without_echoing_upstream_body(self):
        for payload, status in [
            ({"error": {"status_code": "429", "message": "private"}}, 429),
            ({"error": {"status": 503}}, 503),
            ({"error": {"code": None, "type": "overloaded_error"}}, 503),
            ({"error": "private", "status_code": 500}, 500),
            ({"error": {"message": "private"}}, None),
        ]:
            for convert in (lambda value: value, objects):
                with self.subTest(payload=payload, convert=convert):
                    with self.assertRaises(ProviderStreamError) as caught:
                        list(self.parse([convert(payload)]))
                    self.assertEqual(getattr(caught.exception, "status_code", None), status)
                    self.assertNotIn("private", str(caught.exception))

    def test_first_content_is_not_buffered(self):
        def source():
            yield frame({"content": "first"})
            self.fail("consumer has not requested the next chunk")
        stream = self.parse(source())
        self.assertEqual(next(stream), {"type": "content", "content": "first"})
        stream.close()

    def test_missing_index_resolves_known_id_without_merging_parallel_calls(self):
        chunks = [
            frame({"tool_calls": [tool(3, "a", "first"), tool(8, "b", "second")]}),
            frame({"tool_calls": [tool(None, "b", arguments='{}'), tool(None, "a", arguments='{}')]}, "tool_calls"),
        ]
        result = [event for event in self.parse(chunks) if event["type"] == "tool_call"]
        self.assertEqual([event["index"] for event in result], [3, 8, 8, 3])

    def test_single_call_can_omit_index_and_repeat_identity(self):
        chunks = [
            frame({"tool_calls": [tool(None, "a", "first", '{')]}),
            frame({"tool_calls": [tool(None, "a", "first", '}')]}, "tool_calls"),
        ]
        result = list(self.parse(chunks))
        self.assertEqual([event["index"] for event in result[:-1]], [0, 0])

    def test_conflicting_or_ambiguous_tool_identity_fails_without_payload_leak(self):
        cases = [
            ([tool(0, "private-id", "first")], [tool(0, "other-id")]),
            ([tool(0, "private-id", "first")], [tool(1, "private-id")]),
            ([tool(0, "a", "first"), tool(1, "b", "second")], [tool(None, arguments="private-arguments")]),
            ([], [tool(None, "a"), tool(None, "b")]),
            ([tool(0, "a", "first")], [tool(0, name="different")]),
            ([], [tool(-1)]), ([], [tool(True)]), ([], [tool("0")]),
        ]
        for initial, invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ProviderStreamError) as caught:
                    list(self.parse([frame({"tool_calls": initial}), frame({"tool_calls": invalid}, "tool_calls")]))
                self.assertIn("chunk 2", str(caught.exception))
                self.assertNotIn("private-id", str(caught.exception))
                self.assertNotIn("private-arguments", str(caught.exception))

    def test_large_argument_deltas_are_passed_through_without_json_parsing(self):
        part = "x" * 65536
        chunks = [frame({"tool_calls": [tool(0, "a", "write", '{"text":"')]})]
        chunks.extend(frame({"tool_calls": [tool(0, arguments=part)]}) for _ in range(32))
        chunks.append(frame({"tool_calls": [tool(0, arguments='"}')]}, "tool_calls"))
        with patch("core.llm.chat_completions_stream.json.loads", side_effect=AssertionError("incremental JSON parsing")):
            events = list(self.parse(chunks))
        arguments = [event["function"]["arguments"] for event in events if event["type"] == "tool_call"]
        self.assertEqual(arguments, ['{"text":"'] + [part] * 32 + ['"}'])

    def test_object_arguments_keep_existing_json_serialization(self):
        result = list(self.parse([frame({"tool_calls": [tool(0, "a", "read", {"path": "中文"})]}, "tool_calls")]))
        self.assertEqual(result[0]["function"]["arguments"], '{"path": "中文"}')

    def test_interleaved_streams_have_independent_tool_identity(self):
        first = self.parse([frame({"tool_calls": [tool(0, "first", "read", '{')]}), frame({"tool_calls": [tool(None, "first", arguments='}')]}, "tool_calls")])
        second = self.parse([frame({"tool_calls": [tool(0, "second", "write", '{')]}), frame({"tool_calls": [tool(None, "second", arguments='}')]}, "tool_calls")])
        self.assertEqual(next(first)["id"], "first")
        self.assertEqual(next(second)["id"], "second")
        self.assertEqual(next(first)["id"], "first")
        self.assertEqual(next(second)["id"], "second")
        self.assertEqual(next(first)["type"], "provider_terminal")
        self.assertEqual(next(second)["type"], "provider_terminal")

    def test_structured_reasoning_is_not_treated_as_plaintext(self):
        result = list(self.parse([frame({"reasoning_details": [{"data": "encrypted"}], "content": "answer"}, "stop")]))
        self.assertEqual(result[0], {"type": "content", "content": "answer"})

    def test_invalid_fields_report_position(self):
        for invalid in ({"choices": "bad"}, frame({"content": {"secret": "body"}}), frame({"tool_calls": "bad"})):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ProviderStreamError, "chunk 1"):
                    list(self.parse([invalid]))

    def test_repeated_finish_reason_with_usage_keeps_legacy_events(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
        for reason in ("stop", "tool_calls", "length", "content_filter"):
            terminal = {"type": "provider_terminal", "status": "completed" if reason in ("stop", "tool_calls") else "incomplete", "finish_reason": reason}
            for delta in (None, {}, {"content": ""}):
                for convert in (lambda value: value, objects):
                    with self.subTest(reason=reason, delta=delta, convert=convert):
                        chunks = [frame({}, reason), frame(delta, reason, usage=usage)]
                        self.assertEqual(list(self.parse(convert(chunks))), [
                            terminal,
                            {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13, "cache_metrics_status": "unavailable"}},
                            terminal,
                        ])

    def test_deltas_after_finish_reason_are_not_dropped_or_rejected(self):
        chunks = [
            frame({"content": "before"}, "stop"),
            frame({"reasoning_content": "thought", "content": "after"}),
            frame({"tool_calls": [tool(0, "a", "read", '{')]}),
            frame({"tool_calls": [tool(0, arguments='}')]}, "tool_calls"),
        ]
        for convert in (lambda value: value, objects):
            with self.subTest(convert=convert):
                result = list(self.parse(convert(chunks)))
                self.assertEqual([event["type"] for event in result], ["content", "provider_terminal", "reasoning", "content", "tool_call", "tool_call", "provider_terminal"])
                self.assertEqual([event["content"] for event in result if event["type"] == "content"], ["before", "after"])
                self.assertEqual([event["function"]["arguments"] for event in result if event["type"] == "tool_call"], ['{', '}'])

    def test_later_incomplete_terminal_is_not_hidden_by_earlier_stop(self):
        result = list(self.parse([frame({}, "stop"), frame({}, "length")]))
        self.assertEqual([event["status"] for event in result], ["completed", "incomplete"])


class ChatCompletionsIntegrationTests(unittest.TestCase):
    def provider(self, **options):
        client = MagicMock()
        with patch("openai.OpenAI", return_value=client) as constructor:
            provider = OpenAIProvider("test-key", options.pop("base_url", "https://api.openai.com/v1"), options.pop("model_name", "gpt-4.1-mini"), **options)
        return provider, client, constructor.call_args.kwargs

    def test_existing_request_and_connection_parameters_are_unchanged(self):
        for url, model, options in [
            ("https://api.openai.com/v1", "gpt-4.1-mini", {"reasoning_effort": "high"}),
            ("https://api.deepseek.com", "deepseek-v4-pro", {"reasoning_effort": "max", "extra_body": {"thinking": {"type": "enabled"}}}),
            ("https://gateway.example/v1", "custom", {"reasoning_effort": "high"}),
        ]:
            with self.subTest(url=url):
                provider, client, connection = self.provider(base_url=url, model_name=model, reasoning_effort=options["reasoning_effort"], prompt_cache_key_param="prompt_cache_key")
                messages = [{"role": "system", "content": "stable"}, {"role": "user", "content": "read"}]
                tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object", "properties": {}}}}]
                original = copy.deepcopy((messages, tools))
                client.chat.completions.create.return_value = [frame({"content": "ok"}, "stop")]
                list(provider.chat_stream(messages, tools, "cache", {"client_request_id": "request-1"}))
                self.assertEqual(client.chat.completions.create.call_args.kwargs, {
                    "model": model, "messages": messages, "tools": tools, "stream": True,
                    "extra_headers": {"X-Client-Request-Id": "request-1"}, "prompt_cache_key": "cache",
                    "stream_options": {"include_usage": True}, **options,
                })
                self.assertEqual((messages, tools), original)
                expected = {"api_key": "test-key", "base_url": url, "timeout": httpx.Timeout(connect=20, read=660 if "deepseek" in url else 60, write=60, pool=60)}
                if "deepseek" in url:
                    expected["max_retries"] = 0
                self.assertEqual(connection, expected)

    def test_parse_error_releases_stream_and_preserves_partial_output(self):
        provider, client, _ = self.provider()
        source = MagicMock()
        source.__iter__.return_value = iter([frame({"content": "kept"}), {"error": {"code": 503, "message": "private prompt and key"}}])
        client.chat.completions.create.return_value = source
        register, release = MagicMock(return_value=True), MagicMock()
        chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}], request_context={"register_stream": register, "release_stream": release, "client_request_id": "request-1"}))
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(chunks[1], {"type": "content", "content": "kept"})
        self.assertEqual(chunks[-1]["type"], "error")
        self.assertIn("chunk 2", chunks[-1]["content"])
        self.assertIn("503", chunks[-1]["content"])
        self.assertNotIn("private", str(chunks))
        self.assertFalse(any(event["type"] == "provider_terminal" for event in chunks))
        source.close.assert_called_once()
        register.assert_called_once_with(source)
        release.assert_called_once_with(source)

    def test_consumer_close_releases_stream_after_first_content(self):
        provider, client, _ = self.provider()
        source = MagicMock()
        source.__iter__.return_value = iter([frame({"content": "first"}), frame({"content": "second"}, "stop")])
        client.chat.completions.create.return_value = source
        release = MagicMock()
        stream = provider._chat_completions_stream([{"role": "user", "content": "hi"}], request_context={"release_stream": release})
        self.assertEqual(next(stream)["type"], "provider_request")
        self.assertEqual(next(stream), {"type": "content", "content": "first"})
        stream.close()
        source.close.assert_called_once()
        release.assert_called_once_with(source)

    def test_non_transient_or_post_terminal_in_band_error_is_not_retried(self):
        for prefix, error in [
            ([], {"code": "invalid_api_key"}),
            ([], {"code": "invalid_request_error"}),
            ([frame(finish="stop")], {"code": 503}),
        ]:
            with self.subTest(prefix=prefix, error=error):
                provider, client, _ = self.provider()
                client.chat.completions.create.return_value = prefix + [{"error": error}]
                chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
                self.assertEqual(client.chat.completions.create.call_count, 1)
                self.assertEqual(chunks[-1]["type"], "error")

    def test_responses_never_enters_chat_parser(self):
        provider, client, _ = self.provider(api_protocol="responses")
        client.responses.create.return_value = [{"type": "response.completed", "response": {
            "id": "response-1", "status": "completed", "output": [
                {"type": "message", "id": "message-1", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]},
            ],
        }}]
        with patch("core.llm.providers.iter_chat_completions_events", side_effect=AssertionError("Chat parser called by Responses")):
            chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
        self.assertFalse(any(chunk["type"] == "error" for chunk in chunks), chunks)
        self.assertTrue(any(chunk["type"] == "provider_terminal" and chunk["status"] == "completed" for chunk in chunks))
        client.chat.completions.create.assert_not_called()

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_in_band_transient_error_uses_existing_retry_policy(self, wait):
        provider, client, _ = self.provider()
        client.chat.completions.create.side_effect = [
            [{"error": {"code": "rate_limit_exceeded", "message": "secret"}}],
            [frame({"content": "ok"}, "stop")],
        ]
        chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
        self.assertEqual([chunk["attempt"] for chunk in chunks if chunk["type"] == "provider_retry"], [1])
        self.assertEqual([chunk["content"] for chunk in chunks if chunk["type"] == "content"], ["ok"])
        self.assertEqual(client.chat.completions.create.call_args_list[0], client.chat.completions.create.call_args_list[1])
        wait.assert_called_once()

    def test_actual_sdk_sse_heartbeats_and_trailing_usage(self):
        self._assert_actual_sdk_sse_tail(repeated_finish=False)

    def test_actual_sdk_sse_usage_with_repeated_finish_reason(self):
        self._assert_actual_sdk_sse_tail(repeated_finish=True)

    def _assert_actual_sdk_sse_tail(self, repeated_finish):
        from openai import OpenAI
        provider, _, _ = self.provider(reasoning_effort="")
        bodies = []

        def handle(request):
            bodies.append(json.loads(request.content))
            usage = {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
            tail = frame({}, "stop", usage=usage) if repeated_finish else {"choices": [], "usage": usage}
            chunks = [frame({"role": "assistant"}), frame({"reasoning": "thinking"}), frame({"content": "answer"}), frame(finish="stop"), tail]
            data = ": keep-alive\n\n" + "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": "upstream-1"}, text=data)

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with OpenAI(api_key="test-key", base_url="https://mock.invalid/v1", http_client=http_client) as client:
                provider.client = client
                chunks = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
        expected_types = ["provider_request", "reasoning", "content", "provider_terminal", "usage"]
        if repeated_finish:
            expected_types.append("provider_terminal")
        self.assertEqual([event["type"] for event in chunks], expected_types)
        self.assertEqual(chunks[0]["provider_request_id"], "upstream-1")
        self.assertEqual(next(event["usage"] for event in chunks if event["type"] == "usage")["total_tokens"], 6)
        self.assertEqual(bodies, [{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hi"}], "stream": True, "stream_options": {"include_usage": True}}])


if __name__ == "__main__":
    unittest.main()

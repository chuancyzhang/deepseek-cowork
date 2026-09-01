import os
import sys
import unittest
import tempfile
import threading
import time
import httpx
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import (
    clear_reasoning_content,
    find_tool_call_messages_without_reasoning,
    repair_tool_call_sequence,
    sanitize_llm_messages,
)
from core.llm.deepseek import (
    DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY,
    DEEPSEEK_RESPONSES_REPLAY_META_KEY,
    is_official_deepseek_api,
)
from core.llm.providers import (
    API_PROTOCOL_RESPONSES,
    MODEL_API_STREAM_IDLE_TIMEOUT_SECONDS,
    OpenAIProvider,
    ProviderStreamError,
    _wait_before_model_retry,
    is_transient_model_api_error,
)
from core.llm.responses_replay import (
    PROVIDER_REPLAY_NAMESPACE_META_KEY,
    RESPONSES_REPLAY_INPUT_KEY,
    RESPONSES_REPLAY_META_KEY,
    build_provider_replay_namespace,
)


class TestOpenAIProviderDeepSeek(unittest.TestCase):
    def _build_provider(self, **kwargs):
        client = MagicMock()
        openai_module = ModuleType("openai")
        openai_module.OpenAI = MagicMock(return_value=client)
        patcher = patch.dict(sys.modules, {"openai": openai_module})
        mock_openai = patcher.start()
        self.addCleanup(patcher.stop)
        provider = OpenAIProvider(
            api_key="test-key",
            base_url=kwargs.get("base_url", "https://api.deepseek.com"),
            model_name=kwargs.get("model_name", "deepseek-v4-pro"),
            thinking_enabled=kwargs.get("thinking_enabled", True),
            reasoning_effort=kwargs.get("reasoning_effort", "high"),
            supports_vision=kwargs.get("supports_vision", False),
            supports_image_generation=kwargs.get("supports_image_generation", False),
            api_protocol=kwargs.get("api_protocol", "chat_completions"),
        )
        self.assertIn("openai", sys.modules)
        return provider, client

    def test_deepseek_requests_include_thinking_and_reasoning_effort(self):
        provider, client = self._build_provider(reasoning_effort="max")
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return []

        client.chat.completions.create.side_effect = create
        list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(captured["reasoning_effort"], "max")
        self.assertEqual(captured["extra_body"]["thinking"]["type"], "enabled")

    def test_official_deepseek_chat_projects_later_configuration_roles(self):
        provider, _client = self._build_provider()
        messages = [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "task"},
            {"role": "system", "content": "runtime"},
            {"role": "developer", "content": "instruction"},
        ]

        prepared, projection_count = provider._prepare_chat_completions_messages(messages)

        self.assertEqual(
            [message["role"] for message in prepared],
            ["system", "user", "user", "user"],
        )
        self.assertEqual([message["content"] for message in prepared], [
            "stable",
            "task",
            "runtime",
            "instruction",
        ])
        self.assertEqual(projection_count, 2)
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "system", "developer"],
        )

    def test_official_deepseek_chat_projection_keeps_wire_prefix_stable(self):
        provider, _client = self._build_provider()
        first_request = [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "task"},
            {"role": "system", "content": "runtime-1"},
        ]
        second_request = first_request + [
            {"role": "assistant", "content": "working"},
            {"role": "system", "content": "runtime-2"},
        ]

        first_wire, _first_count = provider._prepare_chat_completions_messages(first_request)
        second_wire, _second_count = provider._prepare_chat_completions_messages(second_request)

        self.assertEqual(second_wire[:len(first_wire)], first_wire)

    def test_chat_projection_does_not_change_other_hosts_or_responses(self):
        compatible, _client = self._build_provider(
            base_url="https://compatible.example/v1",
            model_name="compatible-model",
        )
        messages = [
            {"role": "system", "content": "stable"},
            {"role": "developer", "content": "later"},
        ]
        compatible_wire, projection_count = (
            compatible._prepare_chat_completions_messages(messages)
        )
        self.assertEqual([item["role"] for item in compatible_wire], ["system", "developer"])
        self.assertEqual(projection_count, 0)

        responses, _client = self._build_provider(api_protocol=API_PROTOCOL_RESPONSES)
        responses_input = responses._prepare_responses_input(messages)
        self.assertEqual([item["role"] for item in responses_input], ["developer", "developer"])

    def test_openai_stream_read_timeout_is_sixty_seconds(self):
        self._build_provider()
        timeout = sys.modules["openai"].OpenAI.call_args.kwargs["timeout"]
        self.assertEqual(MODEL_API_STREAM_IDLE_TIMEOUT_SECONDS, 60.0)
        self.assertEqual(timeout.read, 60.0)

    def test_chat_completions_blocks_incomplete_tool_round_before_provider_call(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="",
        )

        chunks = list(provider.chat_stream([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "one", "arguments": "{}"},
                    },
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {"name": "two", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "one"},
        ]))

        client.chat.completions.create.assert_not_called()
        self.assertEqual(chunks[0]["type"], "error")
        self.assertIn("call-2", chunks[0]["content"])

    def test_non_deepseek_requests_send_configured_reasoning_effort_without_deepseek_body(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="max",
        )
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return []

        client.chat.completions.create.side_effect = create
        list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(captured["reasoning_effort"], "max")
        self.assertNotIn("extra_body", captured)

    def test_non_deepseek_requests_omit_unconfigured_reasoning_effort(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="",
        )
        captured = {}
        client.chat.completions.create.side_effect = lambda **kwargs: captured.update(kwargs) or []

        list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertNotIn("reasoning_effort", captured)
        self.assertNotIn("extra_body", captured)

    def test_connection_uses_timeout_and_current_reasoning_effort(self):
        provider, client = self._build_provider(reasoning_effort="max")
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )

        result = provider.test_connection(timeout=20)

        self.assertEqual(result, "OK")
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 20)
        self.assertEqual(kwargs["reasoning_effort"], "max")

    def test_responses_protocol_maps_stream_tools_reasoning_and_usage(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            reasoning_effort="max",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = [
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(
                    type="function_call",
                    id="fc_item_1",
                    call_id="call-1",
                    name="lookup",
                    arguments="",
                ),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id="fc_item_1",
                output_index=0,
                delta='{"q":"hi"}',
            ),
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="检查资料"),
            SimpleNamespace(type="response.output_text.delta", delta="完成"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    output=[
                        {
                            "id": "fc_item_1",
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "lookup",
                            "arguments": '{"q":"hi"}',
                            "status": "completed",
                        },
                        {
                            "id": "msg-standard-1",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "完成", "annotations": []}],
                        },
                    ],
                    usage=SimpleNamespace(
                        input_tokens=100,
                        output_tokens=20,
                        input_tokens_details=SimpleNamespace(cached_tokens=60),
                    )
                ),
            ),
        ]

        chunks = list(provider.chat_stream(
            [{"role": "user", "content": "hello"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup data",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
            }],
        ))

        params = client.responses.create.call_args.kwargs
        self.assertEqual(params["reasoning"], {"effort": "max"})
        self.assertEqual(params["input"][0]["content"][0]["type"], "input_text")
        self.assertEqual(params["tools"][0]["name"], "lookup")
        data_chunks = [
            chunk for chunk in chunks
            if chunk["type"] not in {"provider_request", "provider_terminal"}
        ]
        self.assertEqual(data_chunks[0]["id"], "call-1")
        self.assertEqual(data_chunks[1]["function"]["arguments"], '{"q":"hi"}')
        self.assertEqual(data_chunks[2], {"type": "reasoning", "content": "检查资料"})
        self.assertEqual(data_chunks[3], {"type": "content", "content": "完成"})
        replay_chunk = next(chunk for chunk in chunks if chunk["type"] == "response_items")
        self.assertEqual(replay_chunk["items"][1]["id"], "msg-standard-1")
        usage_chunk = next(chunk for chunk in chunks if chunk["type"] == "usage")
        self.assertEqual(usage_chunk["usage"]["cached_input_tokens"], 60)

    def test_responses_protocol_sends_prompt_cache_key_without_explicit_param_config(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = []

        list(provider.chat_stream(
            [{"role": "user", "content": "hello"}],
            prompt_cache_key="conv-1",
        ))

        params = client.responses.create.call_args.kwargs
        self.assertEqual(params["prompt_cache_key"], "conv-1")

    def test_responses_image_generation_tool_and_result_are_stable_and_sanitized(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-image-capable",
            api_protocol=API_PROTOCOL_RESPONSES,
            supports_image_generation=True,
        )
        client.responses.create.return_value = [
            SimpleNamespace(
                type="response.image_generation_call.in_progress",
                item_id="ig_1",
                output_index=0,
            ),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_1",
                    output=[{
                        "type": "image_generation_call",
                        "id": "ig_1",
                        "status": "completed",
                        "result": "Zm9v",
                    }],
                    usage=None,
                ),
            ),
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "画一只猫"}]))

        self.assertEqual(
            client.responses.create.call_args.kwargs["tools"],
            [{"type": "image_generation"}],
        )
        self.assertIn({
            "type": "server_tool_status",
            "name": "image_generation",
            "status": "in_progress",
            "id": "ig_1",
            "output_index": 0,
        }, chunks)
        image_chunk = next(chunk for chunk in chunks if chunk["type"] == "output_image")
        self.assertEqual(image_chunk["item_id"], "ig_1")
        self.assertEqual(image_chunk["image_base64"], "Zm9v")
        replay = next(chunk for chunk in chunks if chunk["type"] == "response_items")
        self.assertEqual(replay["items"], [{"type": "image_generation_call", "id": "ig_1"}])
        self.assertNotIn("result", replay["items"][0])

    def test_image_generation_configuration_rejects_chat_completions(self):
        provider, client = self._build_provider(supports_image_generation=True)

        chunks = list(provider.chat_stream([{"role": "user", "content": "画图"}]))

        self.assertEqual(chunks[0]["type"], "error")
        self.assertIn("Responses", chunks[0]["content"])
        client.chat.completions.create.assert_not_called()

    def test_responses_completed_output_repairs_missing_function_call_stream_events(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = [
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp-complete-tool",
                    output=[
                        {
                            "id": "fc-complete-tool",
                            "type": "function_call",
                            "call_id": "call-complete-tool",
                            "name": "lookup",
                            "arguments": '{"q":"complete"}',
                            "status": "completed",
                        }
                    ],
                    usage=None,
                ),
            )
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        tool_call = next(chunk for chunk in chunks if chunk["type"] == "tool_call")
        self.assertEqual(tool_call["id"], "call-complete-tool")
        self.assertEqual(tool_call["function"]["name"], "lookup")
        self.assertEqual(tool_call["function"]["arguments"], '{"q":"complete"}')
        self.assertEqual(
            next(chunk for chunk in chunks if chunk["type"] == "provider_terminal")["status"],
            "completed",
        )

    def test_responses_completed_output_repairs_missing_text_stream_events(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = [
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp-complete-text",
                    output=[
                        {
                            "id": "msg-complete-text",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "completed text",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    usage=None,
                ),
            )
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(
            "".join(chunk["content"] for chunk in chunks if chunk["type"] == "content"),
            "completed text",
        )
        self.assertEqual(
            next(chunk for chunk in chunks if chunk["type"] == "provider_terminal")["status"],
            "completed",
        )

    def test_responses_completed_output_reconciles_divergent_streamed_text(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = [
            SimpleNamespace(type="response.output_text.delta", delta="partial"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp-divergent-text",
                    output=[
                        {
                            "id": "msg-divergent-text",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "different",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    usage=None,
                ),
            ),
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        snapshot = next(chunk for chunk in chunks if chunk["type"] == "content_snapshot")
        self.assertEqual(snapshot["previous_content"], "partial")
        self.assertEqual(snapshot["content"], "different")
        self.assertFalse(any(chunk["type"] == "error" for chunk in chunks))
        self.assertEqual(
            next(chunk for chunk in chunks if chunk["type"] == "provider_terminal")["status"],
            "completed",
        )

    def test_deepseek_responses_accepts_assistant_message_without_output_id(self):
        provider, _client = self._build_provider(
            base_url="https://api.deepseek.com/v1",
            model_name="deepseek-chat",
            api_protocol=API_PROTOCOL_RESPONSES,
        )

        items = provider._normalize_deepseek_replay_items([
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "ok"}],
            }
        ])

        self.assertEqual(items[0]["content"][0]["text"], "ok")

    def test_responses_protocol_omits_empty_prompt_cache_key(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = []

        list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        params = client.responses.create.call_args.kwargs
        self.assertNotIn("prompt_cache_key", params)

    def test_responses_protocol_maps_tool_history_to_typed_items(self):
        provider, _client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )

        items = provider._prepare_responses_input([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ])

        self.assertEqual(items[0]["type"], "function_call")
        self.assertEqual(items[1], {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "ok",
        })

    def test_responses_connection_uses_responses_endpoint(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            reasoning_effort="medium",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = SimpleNamespace(output_text="OK")

        self.assertEqual(provider.test_connection(timeout=12), "OK")
        params = client.responses.create.call_args.kwargs
        self.assertEqual(params["max_output_tokens"], 8)
        self.assertEqual(params["reasoning"], {"effort": "medium"})

    def test_official_deepseek_responses_replays_completed_items_and_omits_cache_key(self):
        provider, client = self._build_provider(
            model_name="deepseek-v4-flash",
            reasoning_effort="high",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        replay_items = [
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "需要查询本地工具"}],
                "status": "completed",
                "encrypted_content": None,
            },
            {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "lookup",
                "arguments": '{"q":"hi"}',
                "status": "completed",
            },
        ]
        client.responses.create.return_value = [
            SimpleNamespace(type="response.reasoning_text.delta", delta="需要查询本地工具"),
            SimpleNamespace(
                type="response.output_item.added",
                output_index=1,
                item=SimpleNamespace(
                    type="function_call",
                    id="fc_1",
                    call_id="call-1",
                    name="lookup",
                    arguments="",
                ),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id="fc_1",
                output_index=1,
                delta='{"q":"hi"}',
            ),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(output=replay_items, usage=None),
            ),
        ]

        chunks = list(provider.chat_stream(
            [{"role": "user", "content": "hello"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup data",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            prompt_cache_key="conv-1",
        ))

        params = client.responses.create.call_args.kwargs
        self.assertNotIn("prompt_cache_key", params)
        self.assertEqual(params["tools"][-1], {"type": "web_search"})
        self.assertEqual(
            next(chunk for chunk in chunks if chunk["type"] == "response_items")["items"],
            replay_items,
        )

    def test_official_deepseek_responses_replays_saved_items_without_duplication(self):
        provider, _client = self._build_provider(
            model_name="deepseek-v4-flash",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        replay_items = [
            {
                "id": "rs_saved",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "saved reasoning"}],
            },
            {
                "id": "msg_saved",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": "saved answer",
                    "annotations": [],
                }],
            },
        ]

        items = provider._prepare_responses_input([{
            "id": "assistant-1",
            "role": "assistant",
            "content": "must not be duplicated",
            DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY: replay_items,
        }])

        self.assertEqual(items, replay_items)

    def test_official_deepseek_responses_preserves_multi_round_item_order(self):
        provider, _client = self._build_provider(
            model_name="deepseek-v4-flash",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        first_round = [
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "first"}],
            },
            {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "lookup",
                "arguments": '{"round":1}',
            },
        ]
        second_round = [
            {
                "id": "rs_2",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "second"}],
            },
            {
                "id": "ws_2",
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "query": "latest"},
            },
            {
                "id": "fc_2",
                "type": "function_call",
                "call_id": "call-2",
                "name": "lookup",
                "arguments": '{"round":2}',
            },
        ]

        items = provider._prepare_responses_input([
            {
                "role": "assistant",
                "content": "",
                DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY: first_round,
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "one"},
            {
                "role": "assistant",
                "content": "",
                DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY: second_round,
            },
            {"role": "tool", "tool_call_id": "call-2", "content": "two"},
        ])

        self.assertEqual(items, [
            *first_round,
            {"type": "function_call_output", "call_id": "call-1", "output": "one"},
            *second_round,
            {"type": "function_call_output", "call_id": "call-2", "output": "two"},
        ])

    def test_official_deepseek_responses_builds_legacy_reasoning_item_before_function_call(self):
        provider, _client = self._build_provider(
            model_name="deepseek-v4-flash",
            api_protocol=API_PROTOCOL_RESPONSES,
        )

        items = provider._prepare_responses_input([
            {
                "id": "assistant-legacy",
                "role": "assistant",
                "content": "",
                "reasoning_content": "legacy reasoning",
                "tool_calls": [{
                    "id": "call-legacy",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-legacy", "content": "ok"},
        ])

        self.assertEqual([item["type"] for item in items], [
            "reasoning",
            "function_call",
            "function_call_output",
        ])
        self.assertEqual(items[0]["id"], "rs_assistant-legacy")
        self.assertEqual(items[0]["content"], [
            {"type": "reasoning_text", "text": "legacy reasoning"},
        ])

    def test_official_deepseek_responses_rejects_tool_history_without_reasoning(self):
        provider, _client = self._build_provider(
            model_name="deepseek-v4-flash",
            api_protocol=API_PROTOCOL_RESPONSES,
        )

        with self.assertRaisesRegex(ValueError, "缺少 reasoning_text"):
            provider._prepare_responses_input([{
                "id": "assistant-invalid",
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-invalid",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            }])

    def test_official_deepseek_responses_adds_and_deduplicates_native_web_search(self):
        provider, _client = self._build_provider(
            model_name="deepseek-v4-flash",
            api_protocol=API_PROTOCOL_RESPONSES,
        )

        self.assertEqual(provider._prepare_responses_tools([]), [{"type": "web_search"}])
        prepared = provider._prepare_responses_tools([
            {"type": "web_search_2025_08_26"},
            {"type": "web_search"},
        ])
        self.assertEqual(prepared, [{"type": "web_search_2025_08_26"}])

        standard_provider, _client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        self.assertEqual(standard_provider._prepare_responses_tools([
            {"type": "web_search_2025_08_26"},
            {"type": "web_search"},
        ]), [
            {"type": "web_search_2025_08_26"},
            {"type": "web_search"},
        ])

    def test_official_deepseek_responses_emits_server_search_status_without_local_tool_call(self):
        provider, client = self._build_provider(
            model_name="deepseek-v4-flash",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        replay_items = [
            {
                "id": "rs_web",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "需要联网搜索"}],
            },
            {
                "id": "ws_1",
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "query": "today"},
            },
            {
                "id": "msg_web",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "result", "annotations": []}],
            },
        ]
        client.responses.create.return_value = [
            SimpleNamespace(type="response.web_search_call.in_progress", item_id="ws_1"),
            SimpleNamespace(type="response.web_search_call.searching", item_id="ws_1"),
            SimpleNamespace(type="response.web_search_call.completed", item_id="ws_1"),
            SimpleNamespace(type="response.reasoning_text.delta", delta="需要联网搜索"),
            SimpleNamespace(type="response.output_text.delta", delta="result"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(output=replay_items, usage=None),
            ),
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "search"}]))

        self.assertFalse(any(chunk["type"] == "tool_call" for chunk in chunks))
        self.assertEqual(
            [chunk["status"] for chunk in chunks if chunk["type"] == "server_tool_status"],
            ["in_progress", "searching", "completed"],
        )

    def test_official_deepseek_responses_reports_server_search_failure_reason(self):
        provider, client = self._build_provider(
            model_name="deepseek-v4-flash",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        replay_items = [
            {
                "id": "rs_web_failed",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "尝试联网搜索"}],
            },
            {
                "id": "ws_failed",
                "type": "web_search_call",
                "status": "failed",
                "action": {"type": "search", "query": "today"},
                "error": {"message": "search backend unavailable"},
            },
        ]
        client.responses.create.return_value = [
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(output=replay_items, usage=None),
            ),
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "search"}]))

        failed = next(
            chunk
            for chunk in chunks
            if chunk["type"] == "server_tool_status" and chunk["status"] == "failed"
        )
        self.assertEqual(failed["id"], "ws_failed")
        self.assertEqual(failed["reason"], "search backend unavailable")

    def test_standard_openai_responses_ignores_deepseek_replay_metadata(self):
        provider, _client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        items = provider._prepare_responses_input([{
            "id": "assistant-standard",
            "role": "assistant",
            "content": "standard answer",
            "reasoning_content": "deepseek-only",
            DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY: [{
                "id": "rs_deepseek",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "deepseek-only"}],
            }],
        }])

        self.assertEqual(items, [{
            "role": "assistant",
            "content": [{"type": "output_text", "text": "standard answer"}],
        }])

    def test_standard_openai_responses_replays_generic_saved_items_without_duplication(self):
        provider, _client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        replay_items = [
            {
                "id": "rs_standard",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "checked"}],
            },
            {
                "id": "msg_standard",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "saved", "annotations": []}],
            },
        ]

        items = provider._prepare_responses_input([{
            "id": "assistant-standard",
            "role": "assistant",
            "content": "must not be duplicated",
            RESPONSES_REPLAY_INPUT_KEY: replay_items,
        }])

        self.assertEqual(items, replay_items)

    def test_official_deepseek_api_detection_requires_exact_host(self):
        self.assertTrue(is_official_deepseek_api("https://api.deepseek.com/v1"))
        self.assertTrue(is_official_deepseek_api("api.deepseek.com"))
        self.assertFalse(is_official_deepseek_api("https://proxy.example/deepseek"))
        self.assertFalse(is_official_deepseek_api("https://api.deepseek.com.example"))

    def test_non_deepseek_prepare_messages_drops_reasoning_content(self):
        provider, _client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )
        prepared = provider._prepare_messages([
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "drop me",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            }
        ])

        self.assertNotIn("reasoning_content", prepared[0])

    def test_prepare_messages_keeps_reasoning_for_tool_call_turns(self):
        provider, _client = self._build_provider()
        prepared = provider._prepare_messages([
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep me",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            }
        ])

        self.assertIsNone(prepared[0]["content"])
        self.assertEqual(prepared[0]["reasoning_content"], "keep me")

    def test_prepare_messages_keeps_deepseek_assistant_reasoning_without_tool_calls(self):
        provider, _client = self._build_provider()
        prepared = provider._prepare_messages([
            {
                "role": "assistant",
                "content": "final",
                "reasoning_content": "keep final reasoning",
            }
        ])

        self.assertEqual(prepared[0]["reasoning_content"], "keep final reasoning")

    def test_chat_stream_omits_none_tool_call_arguments(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )

        client.chat.completions.create.return_value = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    function=SimpleNamespace(name="text_file_read", arguments=None),
                                )
                            ],
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    function=SimpleNamespace(name=None, arguments='{"path":"a.txt"}'),
                                )
                            ],
                        )
                    )
                ]
            ),
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "read"}]))

        tool_chunks = [chunk for chunk in chunks if chunk["type"] == "tool_call"]
        self.assertEqual(len(tool_chunks), 2)
        self.assertNotIn("arguments", tool_chunks[0]["function"])
        self.assertEqual(tool_chunks[1]["function"]["arguments"], '{"path":"a.txt"}')
        for chunk in tool_chunks:
            if "arguments" in chunk["function"]:
                self.assertIsNot(chunk["function"]["arguments"], None)

    def test_chat_stream_emits_cached_usage_payload(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )

        client.chat.completions.create.return_value = [
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=75),
                ),
                choices=[],
            ),
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            reasoning_content=None,
                            content=None,
                            tool_calls=None,
                        ),
                        finish_reason="stop",
                    )
                ],
            ),
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        usage = next(chunk["usage"] for chunk in chunks if chunk["type"] == "usage")
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["cached_input_tokens"], 75)
        self.assertEqual(usage["uncached_input_tokens"], 25)
        self.assertAlmostEqual(usage["cache_hit_rate"], 0.75)

    def test_chat_stream_emits_official_deepseek_cache_usage_payload(self):
        provider, client = self._build_provider(
            model_name="deepseek-v4-pro",
            thinking_enabled=False,
            reasoning_effort="",
        )
        client.chat.completions.create.return_value = [
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=10_000,
                    completion_tokens=20,
                    total_tokens=10_020,
                    prompt_cache_hit_tokens=8_576,
                    prompt_cache_miss_tokens=1_424,
                ),
                choices=[],
            ),
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            reasoning_content=None,
                            content=None,
                            tool_calls=None,
                        ),
                        finish_reason="stop",
                    )
                ],
            ),
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        usage = next(chunk["usage"] for chunk in chunks if chunk["type"] == "usage")
        self.assertEqual(usage["cached_input_tokens"], 8_576)
        self.assertEqual(usage["uncached_input_tokens"], 1_424)
        self.assertAlmostEqual(usage["cache_hit_rate"], 0.8576)
        self.assertEqual(usage["cache_metrics_status"], "deepseek_prompt_cache")

    def test_prompt_cache_key_requires_explicit_param(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return []

        client.chat.completions.create.side_effect = create
        list(provider.chat_stream([{"role": "user", "content": "hello"}], prompt_cache_key="conv-1"))
        self.assertNotIn("prompt_cache_key", captured)

        provider.prompt_cache_key_param = "prompt_cache_key"
        captured.clear()
        list(provider.chat_stream([{"role": "user", "content": "hello"}], prompt_cache_key="conv-1"))
        self.assertEqual(captured["prompt_cache_key"], "conv-1")

    def test_stream_usage_bad_request_is_not_retried_with_a_new_request(self):
        provider, client = self._build_provider(
            base_url="https://compatible.example/v1",
            model_name="compatible-model",
        )
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            raise Exception("400 Bad Request: unknown parameter")

        client.chat.completions.create.side_effect = create

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(len(calls), 1)
        self.assertTrue(any(chunk["type"] == "error" for chunk in chunks))
        self.assertIn("stream_options", calls[0])

    def test_chat_stream_marks_length_finish_as_incomplete(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )
        client.chat.completions.create.return_value = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        delta=SimpleNamespace(content="partial", tool_calls=None),
                    )
                ]
            )
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        terminal = next(chunk for chunk in chunks if chunk["type"] == "provider_terminal")
        self.assertEqual(terminal["status"], "incomplete")
        self.assertEqual(terminal["finish_reason"], "length")

    def test_chat_stream_marks_content_filter_finish_as_incomplete(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )
        client.chat.completions.create.return_value = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="content_filter",
                        delta=SimpleNamespace(content="", tool_calls=None),
                    )
                ]
            )
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        terminal = next(chunk for chunk in chunks if chunk["type"] == "provider_terminal")
        self.assertEqual(terminal["status"], "incomplete")
        self.assertEqual(terminal["finish_reason"], "content_filter")

    def test_request_ids_are_sent_and_exposed_for_diagnostics(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )

        class _Stream(list):
            response = SimpleNamespace(headers={"x-request-id": "provider-request-1"})

        client.chat.completions.create.return_value = _Stream(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(content="done", tool_calls=None),
                        )
                    ]
                )
            ]
        )

        chunks = list(
            provider.chat_stream(
                [{"role": "user", "content": "hello"}],
                request_context={"client_request_id": "client-request-1"},
            )
        )

        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["extra_headers"],
            {"X-Client-Request-Id": "client-request-1"},
        )
        request = next(chunk for chunk in chunks if chunk["type"] == "provider_request")
        self.assertEqual(request["client_request_id"], "client-request-1")
        self.assertEqual(request["provider_request_id"], "provider-request-1")

    def test_responses_network_eof_without_terminal_is_an_error(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = [
            SimpleNamespace(type="response.output_text.delta", delta="partial")
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertIn({"type": "content", "content": "partial"}, chunks)
        error = next(chunk for chunk in chunks if chunk["type"] == "error")
        self.assertIn("without a terminal event", error["content"])
        self.assertNotIn("background", client.responses.create.call_args.kwargs)

    def test_chat_completions_publishes_first_content_before_stream_finishes(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )

        def response_stream():
            yield SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content="first",
                        tool_calls=None,
                    ),
                    finish_reason=None,
                )],
            )
            yield SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content=None,
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )],
            )

        client.chat.completions.create.return_value = response_stream()
        chunks = provider.chat_stream([{"role": "user", "content": "hello"}])

        self.assertEqual(next(chunks)["type"], "provider_request")
        self.assertEqual(next(chunks), {"type": "content", "content": "first"})
        self.assertEqual(next(chunks)["type"], "provider_terminal")

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_chat_completions_retries_before_semantic_output(self, _wait):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
        )
        success_stream = [
            SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content="recovered",
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )],
            )
        ]
        client.chat.completions.create.side_effect = [
            ConnectionError("connection reset"),
            success_stream,
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertEqual(
            [chunk["attempt"] for chunk in chunks if chunk["type"] == "provider_retry"],
            [1],
        )
        self.assertEqual(
            [chunk["content"] for chunk in chunks if chunk["type"] == "content"],
            ["recovered"],
        )

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_responses_stream_read_error_after_semantic_output_does_not_retry(self, _wait):
        provider, client = self._build_provider(api_protocol=API_PROTOCOL_RESPONSES)

        def failed_stream():
            yield SimpleNamespace(
                type="response.reasoning_text.delta",
                delta="partial reasoning",
            )
            yield SimpleNamespace(type="response.output_text.delta", delta="partial answer")
            yield SimpleNamespace(
                type="response.output_item.added",
                output_index=1,
                item=SimpleNamespace(
                    type="function_call",
                    id="fc-partial",
                    call_id="call-partial",
                    name="lookup",
                    arguments="",
                ),
            )
            yield SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id="fc-partial",
                output_index=1,
                delta='{"q":"partial"}',
            )
            raise RuntimeError("stream_read_error")

        success_stream = [
            SimpleNamespace(type="response.output_text.delta", delta="final"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp-final",
                    output=[{
                        "id": "msg-final",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{
                            "type": "output_text",
                            "text": "final",
                            "annotations": [],
                        }],
                    }],
                    usage=None,
                ),
            ),
        ]
        client.responses.create.side_effect = [failed_stream(), success_stream]
        messages = [{"role": "user", "content": "hello"}]

        chunks = list(provider.chat_stream(messages))

        self.assertEqual(client.responses.create.call_count, 1)
        retries = [chunk for chunk in chunks if chunk["type"] == "provider_retry"]
        self.assertEqual(retries, [])
        self.assertEqual(
            [chunk["content"] for chunk in chunks if chunk["type"] == "content"],
            ["partial answer"],
        )
        self.assertTrue(any(chunk["type"] == "reasoning" for chunk in chunks))
        self.assertTrue(any(chunk["type"] == "tool_call" for chunk in chunks))
        self.assertEqual(
            [chunk["content"] for chunk in chunks if chunk["type"] == "error"],
            ["stream_read_error"],
        )
        request_inputs = [
            call.kwargs["input"] for call in client.responses.create.call_args_list
        ]
        self.assertEqual(request_inputs, [request_inputs[0]])

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_responses_stream_read_error_exhaustion_reports_one_error(self, _wait):
        provider, client = self._build_provider(api_protocol=API_PROTOCOL_RESPONSES)
        client.responses.create.side_effect = RuntimeError("stream_read_error")

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(client.responses.create.call_count, 6)
        self.assertEqual(len([chunk for chunk in chunks if chunk["type"] == "provider_retry"]), 5)
        errors = [chunk for chunk in chunks if chunk["type"] == "error"]
        self.assertEqual(errors, [{"type": "error", "content": "stream_read_error"}])

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_chat_completions_does_not_retry_after_visible_partial_output(
        self,
        _wait,
    ):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="",
        )

        def failed_stream(index):
            yield SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content=f"partial-{index}",
                        tool_calls=None,
                    ),
                    finish_reason=None,
                )],
            )
            raise ConnectionError("connection reset")

        success_stream = [
            SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content="final",
                        tool_calls=None,
                    ),
                    finish_reason=None,
                )],
            ),
            SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content=None,
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )],
            ),
        ]
        client.chat.completions.create.side_effect = [
            failed_stream(index) for index in range(1, 6)
        ] + [success_stream]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertFalse(any(chunk["type"] == "provider_retry" for chunk in chunks))
        self.assertEqual(
            [chunk["content"] for chunk in chunks if chunk["type"] == "content"],
            ["partial-1"],
        )
        self.assertEqual(
            [chunk["content"] for chunk in chunks if chunk["type"] == "error"],
            ["connection reset"],
        )
        request_messages = [
            call.kwargs["messages"] for call in client.chat.completions.create.call_args_list
        ]
        self.assertTrue(all(messages == request_messages[0] for messages in request_messages))

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_model_api_retry_exhaustion_reports_one_final_error(self, _wait):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="",
        )
        client.chat.completions.create.side_effect = ConnectionError("network error")

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(client.chat.completions.create.call_count, 6)
        self.assertEqual(len([chunk for chunk in chunks if chunk["type"] == "provider_retry"]), 5)
        errors = [chunk for chunk in chunks if chunk["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("network error", errors[0]["content"])

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_model_api_retries_current_server_overload_message(self, _wait):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="",
        )
        overload = "Our servers are currently overloaded. Please try again later."
        client.chat.completions.create.side_effect = RuntimeError(overload)

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(client.chat.completions.create.call_count, 6)
        self.assertEqual(len([chunk for chunk in chunks if chunk["type"] == "provider_retry"]), 5)
        errors = [chunk for chunk in chunks if chunk["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn(overload, errors[0]["content"])

    @patch("core.llm.providers._wait_before_model_retry", return_value=False)
    def test_model_api_retry_wait_stops_without_final_error(self, _wait):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="",
        )
        client.chat.completions.create.side_effect = TimeoutError("request timeout")

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual([chunk["type"] for chunk in chunks], ["provider_retry"])

    def test_model_retry_backoff_is_cancelled_promptly(self):
        stopped = threading.Event()
        result = []

        thread = threading.Thread(
            target=lambda: result.append(
                _wait_before_model_retry(
                    30,
                    {"abort_check": stopped.is_set},
                )
            )
        )
        started_at = time.monotonic()
        thread.start()
        time.sleep(0.05)
        stopped.set()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [False])
        self.assertLess(time.monotonic() - started_at, 0.5)

    def test_model_retry_classification_excludes_request_and_context_errors(self):
        self.assertTrue(is_transient_model_api_error(ConnectionError("connection reset")))
        self.assertTrue(is_transient_model_api_error(httpx.ReadTimeout("stream idle")))
        self.assertTrue(is_transient_model_api_error(RuntimeError("stream_read_error")))
        self.assertTrue(
            is_transient_model_api_error(
                SimpleNamespace(status_code=599, __str__=lambda self: "upstream")
            )
        )
        self.assertTrue(
            is_transient_model_api_error(
                ProviderStreamError("upstream unavailable")
            )
        )
        self.assertFalse(is_transient_model_api_error(ValueError("invalid messages")))
        self.assertFalse(
            is_transient_model_api_error(
                RuntimeError("maximum context length exceeded")
            )
        )

    @patch("core.llm.providers._wait_before_model_retry", return_value=True)
    def test_retry_after_header_takes_priority_over_backoff(self, wait):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="",
        )

        class RateLimitError(RuntimeError):
            status_code = 429
            headers = {"Retry-After": "3"}

        success_stream = [
            SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content="done",
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )],
            )
        ]
        client.chat.completions.create.side_effect = [
            RateLimitError("rate limited"),
            success_stream,
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        retry = next(chunk for chunk in chunks if chunk["type"] == "provider_retry")
        self.assertEqual(retry["delay_seconds"], 3.0)
        wait.assert_called_once_with(3.0, {})

    def test_responses_incomplete_records_terminal_before_error(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = [
            SimpleNamespace(
                type="response.incomplete",
                sequence_number=7,
                response=SimpleNamespace(
                    id="resp-1",
                    error=None,
                    incomplete_details="max_output_tokens",
                ),
            )
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        terminal = next(chunk for chunk in chunks if chunk["type"] == "provider_terminal")
        self.assertEqual(terminal["status"], "incomplete")
        self.assertEqual(terminal["response_id"], "resp-1")
        self.assertEqual(terminal["provider_sequence"], 7)
        self.assertTrue(any(chunk["type"] == "error" for chunk in chunks))

    def test_responses_failed_records_terminal_before_error(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6",
            api_protocol=API_PROTOCOL_RESPONSES,
        )
        client.responses.create.return_value = [
            SimpleNamespace(
                type="response.failed",
                sequence_number=8,
                response=SimpleNamespace(
                    id="resp-failed",
                    error=SimpleNamespace(message="upstream unavailable"),
                ),
            )
        ]

        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        terminal = next(chunk for chunk in chunks if chunk["type"] == "provider_terminal")
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(terminal["response_id"], "resp-failed")
        self.assertEqual(terminal["provider_sequence"], 8)
        self.assertEqual(terminal["error"], "upstream unavailable")
        self.assertTrue(any(chunk["type"] == "error" for chunk in chunks))

    def test_prepare_messages_converts_input_image_parts_when_vision_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "sample.png")
            with open(image_path, "wb") as handle:
                handle.write(
                    bytes.fromhex(
                        "89504E470D0A1A0A0000000D4948445200000001000000010802000000907753DE0000000C4944415408D763F8FFFF3F0005FE02FEA7B90D2F0000000049454E44AE426082"
                    )
                )

            provider, _client = self._build_provider(
                base_url="https://api.openai.com/v1",
                model_name="gpt-4.1-mini",
                supports_vision=True,
            )
            prepared = provider._prepare_messages(
                [
                    {
                        "role": "user",
                        "content": "Read this screenshot",
                        "content_parts": [
                            {"type": "text", "text": "Read this screenshot"},
                            {"type": "input_image", "path": image_path, "name": "sample.png"},
                        ],
                    }
                ]
            )

        self.assertIsInstance(prepared[0]["content"], list)
        self.assertEqual(prepared[0]["content"][0], {"type": "text", "text": "Read this screenshot"})
        self.assertEqual(prepared[0]["content"][1]["type"], "image_url")
        self.assertTrue(prepared[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_prepare_messages_ignores_input_image_parts_when_vision_disabled(self):
        provider, _client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            supports_vision=False,
        )
        prepared = provider._prepare_messages(
            [
                {
                    "role": "user",
                    "content": "Path only",
                    "content_parts": [
                        {"type": "input_image", "path": "C:\\demo.png", "name": "demo.png"},
                    ],
                }
            ]
        )

        self.assertEqual(prepared[0]["content"], "Path only")

    def test_prepare_messages_inlines_text_file_parts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "brief.md")
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("# Brief\nUse this content.")

            provider, _client = self._build_provider(
                base_url="https://api.openai.com/v1",
                model_name="gpt-4.1-mini",
            )
            prepared = provider._prepare_messages(
                [
                    {
                        "role": "user",
                        "content": "Summarize the attached file",
                        "content_parts": [
                            {"type": "input_file", "path": file_path, "name": "brief.md"},
                        ],
                    }
                ]
            )

        self.assertIsInstance(prepared[0]["content"], list)
        self.assertEqual(prepared[0]["content"][0]["text"], "Summarize the attached file")
        self.assertIn("[Attached file: brief.md]", prepared[0]["content"][1]["text"])
        self.assertIn("# Brief\nUse this content.", prepared[0]["content"][1]["text"])

    def test_prepare_messages_marks_large_file_without_inlining(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "large.txt")
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("x" * (129 * 1024))

            provider, _client = self._build_provider(
                base_url="https://api.openai.com/v1",
                model_name="gpt-4.1-mini",
            )
            prepared = provider._prepare_messages(
                [
                    {
                        "role": "user",
                        "content": "Read this",
                        "content_parts": [
                            {"type": "input_file", "path": file_path, "name": "large.txt"},
                        ],
                    }
                ]
            )

        file_text = prepared[0]["content"][1]["text"]
        self.assertIn("Content was not inlined", file_text)
        self.assertIn("larger than 131072 bytes", file_text)


class TestDeepSeekMessageSanitization(unittest.TestCase):
    def test_clear_reasoning_content_preserves_tool_call_turns_only(self):
        cleaned = clear_reasoning_content([
            {"role": "user", "content": "plain turn"},
            {"role": "assistant", "content": "plain", "reasoning_content": "drop"},
            {"role": "user", "content": "tool turn"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
        ])

        self.assertNotIn("reasoning_content", cleaned[1])
        self.assertEqual(cleaned[3]["reasoning_content"], "keep")

    def test_sanitize_llm_messages_preserves_reasoning_for_tool_turns(self):
        sanitized = sanitize_llm_messages([
            {"role": "user", "content": "use a tool"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep",
                "reasoning": "ui-only",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {
                "role": "assistant",
                "content": "final",
                "reasoning_content": "keep final",
            },
            {"role": "user", "content": "plain followup"},
            {"role": "assistant", "content": "plain", "reasoning_content": "drop"},
        ])

        self.assertEqual(sanitized[1]["reasoning_content"], "keep")
        self.assertNotIn("reasoning", sanitized[1])
        self.assertEqual(sanitized[3]["reasoning_content"], "keep final")
        self.assertNotIn("reasoning_content", sanitized[5])

    def test_sanitize_deepseek_responses_preserves_all_reasoning_and_replay_items(self):
        replay_items = [
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "keep all"}],
            },
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "done", "annotations": []}],
            },
        ]
        sanitized = sanitize_llm_messages(
            [
                {"role": "user", "content": "plain"},
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "done",
                    "reasoning_content": "keep all",
                    "reasoning": "ui copy",
                    "meta": {
                        DEEPSEEK_RESPONSES_REPLAY_META_KEY: replay_items,
                        "ui_stage_id": "stage-1",
                    },
                },
            ],
            preserve_all_reasoning=True,
            preserve_responses_replay=True,
            preserve_legacy_deepseek_replay=True,
        )

        self.assertEqual(sanitized[1]["reasoning_content"], "keep all")
        self.assertEqual(sanitized[1][DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY], replay_items)
        self.assertNotIn("reasoning", sanitized[1])
        self.assertNotIn("meta", sanitized[1])

    def test_sanitize_standard_responses_uses_only_generic_replay_metadata(self):
        replay_items = [{
            "id": "msg_standard",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "done", "annotations": []}],
        }]
        sanitized = sanitize_llm_messages(
            [{
                "role": "assistant",
                "content": "done",
                "meta": {RESPONSES_REPLAY_META_KEY: replay_items},
            }],
            preserve_responses_replay=True,
        )

        self.assertEqual(sanitized[0][RESPONSES_REPLAY_INPUT_KEY], replay_items)
        self.assertNotIn(DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY, sanitized[0])

    def test_sanitize_deepseek_responses_accepts_function_call_only_replay(self):
        namespace = build_provider_replay_namespace(
            provider_family="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            protocol="responses",
        )
        replay_items = [{
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call-1",
            "name": "demo",
            "arguments": "{}",
        }]
        sanitized, metadata = sanitize_llm_messages(
            [
                {"role": "user", "content": "use tool"},
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }],
                    "meta": {
                        DEEPSEEK_RESPONSES_REPLAY_META_KEY: replay_items,
                        PROVIDER_REPLAY_NAMESPACE_META_KEY: namespace,
                    },
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            ],
            require_reasoning_replay=True,
            return_metadata=True,
            preserve_all_reasoning=True,
            preserve_responses_replay=True,
            preserve_legacy_deepseek_replay=True,
            strict_reasoning_replay=True,
            target_replay_namespace=namespace,
        )

        self.assertEqual(len(sanitized), 3)
        self.assertEqual(
            sanitized[1][DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY],
            replay_items,
        )
        self.assertNotIn("meta", sanitized[1])
        self.assertNotIn(PROVIDER_REPLAY_NAMESPACE_META_KEY, sanitized[1])
        self.assertEqual(metadata["protocol_tool_round_projections"], [])

    def test_sanitize_deepseek_responses_rejects_missing_function_result(self):
        replay_items = [
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "use tool"}],
            },
            {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "demo",
                "arguments": "{}",
            },
        ]

        with self.assertRaisesRegex(ValueError, "缺少对应的 function_call_output"):
            sanitize_llm_messages(
                [
                    {"role": "user", "content": "use tool"},
                    {
                        "id": "assistant-1",
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "use tool",
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "demo", "arguments": "{}"},
                        }],
                        "meta": {DEEPSEEK_RESPONSES_REPLAY_META_KEY: replay_items},
                    },
                ],
                require_reasoning_replay=True,
                preserve_all_reasoning=True,
                preserve_responses_replay=True,
                strict_reasoning_replay=True,
            )

    def test_find_tool_call_rounds_without_reasoning_does_not_mutate(self):
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]
        missing = find_tool_call_messages_without_reasoning(messages)

        self.assertEqual(len(messages), 4)
        self.assertEqual(missing[0]["tool_call_ids"], ["call-1"])

    def test_sanitize_llm_messages_projects_ui_only_reasoning_in_request_copy(self):
        namespace = build_provider_replay_namespace(
            provider_family="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            protocol="chat_completions",
        )
        source = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "reasoning": "ui-only",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "final"},
        ]
        sanitized, metadata = sanitize_llm_messages([
                *source,
            ], require_reasoning_replay=True, return_metadata=True,
            target_replay_namespace=namespace)

        self.assertEqual(len(sanitized), 3)
        self.assertIn("已完成的历史工具记录", sanitized[1]["content"])
        self.assertEqual(source[1]["reasoning"], "ui-only")
        self.assertIn("tool_calls", source[1])
        self.assertEqual(
            metadata["protocol_tool_round_projections"][0]["tool_call_ids"],
            ["call-1"],
        )

    def test_sanitize_llm_messages_does_not_prune_when_reasoning_replay_not_required(self):
        sanitized = sanitize_llm_messages([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ])

        self.assertEqual(len(sanitized), 2)
        self.assertEqual(sanitized[0]["tool_calls"][0]["id"], "call-1")

    def test_repair_tool_call_sequence_keeps_valid_assistant_tool_roundtrip(self):
        repaired = repair_tool_call_sequence([
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ])

        self.assertEqual(len(repaired), 3)
        self.assertEqual(repaired[0]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(repaired[1]["tool_call_id"], "call-1")


if __name__ == "__main__":
    unittest.main()

